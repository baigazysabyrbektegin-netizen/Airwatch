"""Safely extract the US Embassy archive into data/interim/embassy/extracted/.

The original RAR is never modified. Extraction uses the OS ``tar`` (bsdtar/libarchive), which reads
RAR; no extra dependency is needed. Before extracting we (1) verify the archive checksum against the
Zenodo manifest, (2) list the members and reject unsafe names, and (3) refuse to overwrite any
existing file whose content differs from what we extract.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pipeline import manifest as mf


class ExtractionError(Exception):
    pass


@dataclass(frozen=True)
class Member:
    name: str
    size: int


def tar_version(tar: str = "tar") -> str:
    out = subprocess.run([tar, "--version"], capture_output=True, text=True, check=False)
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else "unknown"


_LIST_RE = re.compile(r"^\S+\s+\d+\s+\S+\s+\S+\s+(\d+)\s+\w{3}\s+\d+\s+(?:\d{4}|\d{1,2}:\d{2})\s+(.+)$")


def list_members(archive: Path, tar: str = "tar") -> list[Member]:
    """List archive members with sizes (no extraction) and validate their names."""
    res = subprocess.run([tar, "-tvf", str(archive)], capture_output=True, text=True)
    if res.returncode != 0:
        raise ExtractionError(f"Cannot list {archive}: {res.stderr.strip() or res.stdout.strip()}")
    members: list[Member] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        m = _LIST_RE.match(line)
        if not m:
            raise ExtractionError(f"Unrecognised archive listing line: {line!r}")
        size, name = int(m.group(1)), m.group(2).strip()
        if line.startswith("d"):
            continue
        members.append(Member(name, size))
    for mem in members:
        n = mem.name
        if n.startswith(("/", "\\")) or ".." in Path(n).parts or ":" in n or Path(n).name != n:
            raise ExtractionError(f"Unsafe or nested member name in archive: {n!r}")
    if not members:
        raise ExtractionError(f"Archive {archive} contains no files.")
    return members


def verify_archive(archive: Path, expected_sha256: str | None, expected_md5: str | None) -> dict[str, str]:
    calc = mf.hash_file(archive, ("md5", "sha256"))
    if expected_sha256 and calc["sha256"] != expected_sha256:
        raise ExtractionError(f"Archive SHA-256 differs from the Zenodo manifest: {archive}")
    if expected_md5 and calc["md5"] != expected_md5:
        raise ExtractionError(f"Archive MD5 differs from the Zenodo manifest: {archive}")
    return calc


def extract_archive(archive: Path, out_dir: Path, tar: str = "tar") -> list[dict]:
    """Extract ``archive`` into ``out_dir``. Returns one dict per extracted file.

    Idempotent: files that already exist with identical size and content are kept. A different
    existing file aborts the run (never overwritten)."""
    members = list_members(archive, tar)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="extract_", dir=out_dir.parent) as tmp:
        tmp_dir = Path(tmp)
        res = subprocess.run([tar, "-xf", str(archive), "-C", str(tmp_dir)], capture_output=True, text=True)
        if res.returncode != 0:
            raise ExtractionError(f"Extraction failed: {res.stderr.strip() or res.stdout.strip()}")
        results: list[dict] = []
        for mem in members:
            src = tmp_dir / mem.name
            if not src.is_file():
                raise ExtractionError(f"Member missing after extraction: {mem.name}")
            if src.stat().st_size != mem.size:
                raise ExtractionError(
                    f"Size mismatch for {mem.name}: archive says {mem.size}, extracted {src.stat().st_size}"
                )
            calc = mf.hash_file(src, ("md5", "sha256"))
            dest = out_dir / mem.name
            if dest.exists():
                if mf.hash_file(dest, ("sha256",))["sha256"] != calc["sha256"]:
                    raise ExtractionError(
                        f"{dest} exists with different content; refusing to overwrite. "
                        "Move it away if you really want to re-extract."
                    )
                status = "already_extracted_verified"
            else:
                shutil.move(str(src), str(dest))
                status = "extracted"
            results.append(
                {
                    "filename": mem.name,
                    "size_bytes": mem.size,
                    "md5": calc["md5"],
                    "sha256": calc["sha256"],
                    "status": status,
                    "local_path": dest.as_posix(),
                }
            )
    return results
