"""Download and verify the files of a public Zenodo record, and write a provenance manifest.

Usage (from the repository root, inside the backend virtual environment):

    python -m pipeline.ingest_zenodo --dry-run      # list files and sizes, change nothing
    python -m pipeline.ingest_zenodo                # download, verify, write the manifest

Raw files are stored unchanged under ``data/raw/zenodo_<id>/``. Archives are NOT extracted.
No Zenodo token is needed or sent: the record is public.
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from pipeline import manifest as mf

DEFAULT_RECORD_ID = "12720883"
API_RECORDS = "https://zenodo.org/api/records/"
RECORDS_HTML = "https://zenodo.org/records/"
PIPELINE_VERSION = "0.1.0"
USER_AGENT = (
    f"AirWatchAI-ingest/{PIPELINE_VERSION} (academic research prototype; "
    "public Zenodo record download; python-httpx)"
)
REPO_ROOT = Path(__file__).resolve().parents[1]
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class IngestError(Exception):
    """Base class for clear, user-facing pipeline failures."""


class MetadataError(IngestError):
    pass


class DownloadError(IngestError):
    pass


class IntegrityError(IngestError):
    pass


class _RetryableHTTP(Exception):
    def __init__(self, status: int, retry_after: float | None = None):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.retry_after = retry_after


@dataclass(frozen=True)
class ZenodoFile:
    key: str
    size: int
    algo: str
    digest: str
    url: str


@dataclass
class DownloadResult:
    status: str  # downloaded_verified | already_present_verified
    calculated: dict[str, str]
    downloaded_at: str | None
    verified_at: str
    resumed: bool = False
    attempts: int = 1


def make_client(timeout_s: float = 60.0) -> httpx.Client:
    timeout = httpx.Timeout(timeout_s, connect=15.0)
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*"},
        timeout=timeout,
        follow_redirects=True,
    )


def _retry_after(resp: httpx.Response) -> float | None:
    try:
        return float(resp.headers.get("Retry-After", ""))
    except ValueError:
        return None


def fetch_record(
    client: httpx.Client,
    record_id: str,
    *,
    retries: int = 4,
    backoff: float = 1.5,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """GET the record JSON from the Zenodo REST API, with retries."""
    url = f"{API_RECORDS}{record_id}"
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            resp = client.get(url)
            if resp.status_code in RETRYABLE_STATUS:
                raise _RetryableHTTP(resp.status_code, _retry_after(resp))
            if resp.status_code != 200:
                raise MetadataError(f"Zenodo metadata request failed: HTTP {resp.status_code} for {url}")
            record = resp.json()
            if not isinstance(record, dict) or "files" not in record or "metadata" not in record:
                raise MetadataError(f"Unexpected Zenodo response shape for {url} (no 'files'/'metadata').")
            return record
        except (httpx.TransportError, _RetryableHTTP, ValueError) as exc:
            last = exc
            if attempt < retries:
                wait = getattr(exc, "retry_after", None) or backoff * 2**attempt
                sleep(wait)
    raise MetadataError(f"Could not fetch Zenodo metadata from {url} after {retries + 1} attempts: {last}")


def list_files(record: dict) -> list[ZenodoFile]:
    """Extract the official file list (name, size, checksum, download URL) from record JSON."""
    files = record.get("files")
    if not isinstance(files, list) or not files:
        raise MetadataError("Zenodo record lists no files.")
    out: list[ZenodoFile] = []
    for f in files:
        try:
            key = f["key"]
            size = int(f["size"])
            algo, digest = mf.parse_checksum(f["checksum"])
            url = f["links"]["self"]
        except (KeyError, TypeError, ValueError) as exc:
            raise MetadataError(f"Malformed file entry in Zenodo metadata: {f!r} ({exc})") from exc
        if Path(key).name != key or key in {"", ".", ".."}:
            raise MetadataError(f"Refusing unsafe file name from metadata: {key!r}")
        out.append(ZenodoFile(key=key, size=size, algo=algo, digest=digest, url=url))
    names = [f.key for f in out]
    if len(set(names)) != len(names):
        raise MetadataError("Zenodo record contains duplicate file names.")
    return out


def _fmt_mb(n: int) -> str:
    return f"{n / 1e6:,.2f} MB"


def print_listing(files: list[ZenodoFile], out=print) -> int:
    total = sum(f.size for f in files)
    out(f"{'file':<48} {'bytes':>12}  checksum")
    for f in files:
        out(f"{f.key:<48} {f.size:>12,}  {f.algo}:{f.digest}")
    out(f"\n{len(files)} files, {total:,} bytes ({_fmt_mb(total)})")
    return total


def _hash_matches(path: Path, f: ZenodoFile) -> tuple[bool, dict[str, str]]:
    calc = mf.hash_file(path, tuple(dict.fromkeys(("md5", "sha256", f.algo))))
    return path.stat().st_size == f.size and calc[f.algo] == f.digest, calc


def _stream_to_part(
    client: httpx.Client, f: ZenodoFile, part: Path, pos: int
) -> tuple[bool, int]:
    """Stream the file into ``part`` (resuming from ``pos`` when the server allows it).

    Returns (resumed, bytes_written_total)."""
    headers = {"Range": f"bytes={pos}-"} if pos > 0 else {}
    with client.stream("GET", f.url, headers=headers) as resp:
        if resp.status_code in RETRYABLE_STATUS:
            raise _RetryableHTTP(resp.status_code, _retry_after(resp))
        if resp.status_code == 416:  # our partial file is not usable; start over
            part.unlink(missing_ok=True)
            raise _RetryableHTTP(416)
        if resp.status_code == 206:
            mode, resumed = "ab", True
        elif resp.status_code == 200:
            mode, resumed = "wb", False  # server ignored Range; restart from zero
        else:
            raise DownloadError(f"Download of {f.key} failed: HTTP {resp.status_code} from {f.url}")
        with open(part, mode) as fh:
            for chunk in resp.iter_bytes(chunk_size=mf.CHUNK):
                fh.write(chunk)
    return resumed, part.stat().st_size


def download_file(
    client: httpx.Client,
    f: ZenodoFile,
    dest_dir: Path,
    *,
    retries: int = 4,
    backoff: float = 1.5,
    force: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> DownloadResult:
    final = dest_dir / f.key
    part = dest_dir / (f.key + ".part")

    if final.exists() and not force:
        ok, calc = _hash_matches(final, f)
        if ok:
            return DownloadResult("already_present_verified", calc, None, mf.utc_now())
        raise IntegrityError(
            f"{final} already exists but does not match Zenodo (size/checksum). "
            "Refusing to overwrite; move it away or re-run with --force."
        )

    attempts = 0
    resumed_any = False
    while True:
        attempts += 1
        try:
            pos = part.stat().st_size if part.exists() else 0
            if pos > f.size:
                part.unlink()
                pos = 0
            if pos < f.size:
                resumed, _ = _stream_to_part(client, f, part, pos)
                resumed_any = resumed_any or resumed
            break
        except (httpx.TransportError, _RetryableHTTP) as exc:
            if attempts > retries:
                raise DownloadError(
                    f"Download of {f.key} failed after {attempts} attempts: {exc}. "
                    f"Partial data kept at {part} (a re-run will resume)."
                ) from exc
            sleep(getattr(exc, "retry_after", None) or backoff * 2 ** (attempts - 1))

    ok, calc = _hash_matches(part, f)
    if not ok:
        size = part.stat().st_size
        part.unlink(missing_ok=True)
        raise IntegrityError(
            f"Verification failed for {f.key}: expected {f.size} bytes and {f.algo}={f.digest}; "
            f"got {size} bytes and {f.algo}={calc[f.algo]}. The partial file was removed."
        )
    now = mf.utc_now()
    os.replace(part, final)
    return DownloadResult("downloaded_verified", calc, now, now, resumed_any, attempts)


def run(
    *,
    record_id: str,
    dest_dir: Path,
    manifest_path: Path,
    dry_run: bool,
    force: bool,
    retries: int,
    timeout: float,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    out: Callable[[str], None] = print,
) -> int:
    own_client = client is None
    client = client or make_client(timeout)
    try:
        retrieved_at = mf.utc_now()
        try:
            record = fetch_record(client, record_id, retries=retries, sleep=sleep)
            files = list_files(record)
        except MetadataError as exc:
            out(f"ERROR: {exc}")
            return 2

        out(f"Record {record_id}: {record['metadata'].get('title')}")
        out(f"Licence (record level): {record['metadata'].get('license')}")
        print_listing(files, out)
        if dry_run:
            out("\nDry run: nothing downloaded, no files or manifest written.")
            return 0

        dest_dir.mkdir(parents=True, exist_ok=True)
        previous = mf.load_manifest(manifest_path) or {}
        prev_files = {e["filename"]: e for e in previous.get("files", [])}

        record_section = mf.build_record_section(
            record,
            record_url=f"{RECORDS_HTML}{record_id}",
            api_url=f"{API_RECORDS}{record_id}",
            retrieved_at=retrieved_at,
        )
        description_text = record_section["description_text"]
        entries, failures = [], 0
        for f in files:
            rel = (dest_dir / f.key)
            try:
                rel_str = rel.relative_to(REPO_ROOT).as_posix()
            except ValueError:
                rel_str = rel.as_posix()
            out(f"\n[{f.key}] {_fmt_mb(f.size)}")
            result: DownloadResult | None = None
            error: str | None = None
            try:
                result = download_file(client, f, dest_dir, retries=retries, force=force, sleep=sleep)
                out(f"  {result.status}  ({f.algo} ok)")
            except IngestError as exc:
                failures += 1
                error = str(exc)
                out(f"  FAILED: {error}")

            notes: list[str] = []
            downloaded_at = result.downloaded_at if result else None
            if result and result.status == "already_present_verified":
                old = prev_files.get(f.key, {})
                same = old.get("zenodo_checksum", {}).get("value") == f.digest
                downloaded_at = old.get("downloaded_at_utc") if same else None
                if downloaded_at is None:
                    notes.append("File was present before this manifest entry; original download time unknown.")
            if result and result.resumed:
                notes.append("Download was resumed from a partial file.")
            entries.append(
                mf.build_file_entry(
                    filename=f.key,
                    size_bytes=f.size,
                    algo=f.algo,
                    expected_digest=f.digest,
                    download_url=f.url,
                    local_path=rel_str,
                    status=result.status if result else "failed",
                    calculated=result.calculated if result else None,
                    downloaded_at=downloaded_at,
                    verified_at=result.verified_at if result else None,
                    error=error,
                    record_licence=record["metadata"].get("license"),
                    description_text=description_text,
                    notes=notes,
                )
            )

        manifest = {
            "schema_version": mf.SCHEMA_VERSION,
            "generated_at_utc": mf.utc_now(),
            "pipeline": {
                "name": "airwatch-ingest-zenodo",
                "version": PIPELINE_VERSION,
                "python": platform.python_version(),
                "httpx": httpx.__version__,
            },
            "record": record_section,
            "files_count": len(entries),
            "files_total_bytes": sum(e["size_bytes"] for e in entries),
            "files_failed": failures,
            "files": entries,
        }
        mf.write_manifest_atomic(manifest_path, manifest)
        out(f"\nManifest written: {manifest_path}")
        if failures:
            out(f"{failures} file(s) FAILED. See the manifest 'error' fields. Exit status 1.")
            return 1
        out("All files downloaded/verified.")
        return 0
    finally:
        if own_client:
            client.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--record-id", default=DEFAULT_RECORD_ID)
    p.add_argument("--dest", type=Path, help="default: data/raw/zenodo_<record-id>/")
    p.add_argument("--manifest", type=Path, help="default: data/manifest/zenodo_<record-id>.json")
    p.add_argument("--dry-run", action="store_true", help="list files and sizes only")
    p.add_argument("--force", action="store_true", help="re-download even if a file already exists")
    p.add_argument("--retries", type=int, default=4)
    p.add_argument("--timeout", type=float, default=60.0, help="read timeout in seconds")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    dest = a.dest or REPO_ROOT / "data" / "raw" / f"zenodo_{a.record_id}"
    manifest_path = a.manifest or REPO_ROOT / "data" / "manifest" / f"zenodo_{a.record_id}.json"
    return run(
        record_id=a.record_id,
        dest_dir=dest,
        manifest_path=manifest_path,
        dry_run=a.dry_run,
        force=a.force,
        retries=a.retries,
        timeout=a.timeout,
    )


if __name__ == "__main__":
    sys.exit(main())
