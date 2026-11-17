"""Manifest helpers: hashing, Zenodo metadata -> manifest sections, atomic JSON writes.

Nothing here invents metadata. Where Zenodo does not state something (version, file-level
licence, upstream origin) the manifest says so explicitly instead of guessing.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
CHUNK = 1024 * 1024

# Words that, when found in the record's own description of a file, indicate the data may
# originate from a third party whose terms are not stated in the Zenodo metadata.
THIRD_PARTY_MARKERS = (
    "openstreetmap",
    "open street map",
    "embassy",
    "clarity",
    "purpleair",
    "purple air",
    "airkaz",
    "landscan",
    "worldpop",
    "ghsl",
    "copernicus",
    "google earth",
    # Gridded population is always derived from an external population product, which the
    # record does not name.
    "population",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def strip_html(text: str | None) -> str:
    """Convert Zenodo's HTML description to plain text, keeping paragraph/list breaks."""
    if not text:
        return ""
    text = re.sub(r"(?i)<\s*br\s*/?>|</\s*(p|li|ul|ol|h\d)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def parse_checksum(value: str) -> tuple[str, str]:
    """Split Zenodo's ``"md5:abcd..."`` into ``("md5", "abcd...")``."""
    algo, sep, digest = (value or "").partition(":")
    if not sep or not algo or not digest:
        raise ValueError(f"Unrecognised checksum format: {value!r}")
    algo = algo.lower()
    if algo not in hashlib.algorithms_available:
        raise ValueError(f"Unsupported checksum algorithm: {algo!r}")
    return algo, digest.lower()


def hash_file(path: Path, algorithms: tuple[str, ...] = ("md5", "sha256")) -> dict[str, str]:
    """Stream a file through the requested hash algorithms (never loads it fully into memory)."""
    hashers = {a: hashlib.new(a) for a in algorithms}
    with open(path, "rb") as fh:
        while chunk := fh.read(CHUNK):
            for h in hashers.values():
                h.update(chunk)
    return {a: h.hexdigest() for a, h in hashers.items()}


def description_excerpt(description_text: str, filename: str) -> str | None:
    """Return the record text near ``filename`` (or None if the file is not mentioned).

    That is the group heading above the file (nearest earlier line that is not itself a
    "File:" line), the line naming the file, and the lines after it up to the next "File:"
    line. Neighbouring items can leak in; this is context for a human reviewer, not proof of
    origin. Only text from the Zenodo record is used."""
    lines = description_text.splitlines()
    out: list[str] = []
    for i, ln in enumerate(lines):
        if filename.lower() not in ln.lower():
            continue
        j = i - 1
        while j >= 0 and "file:" in lines[j].lower():
            j -= 1
        if j >= 0:
            out.append(lines[j])
        out.append(ln)
        for nxt in lines[i + 1 : i + 13]:
            if "file:" in nxt.lower():
                break
            out.append(nxt)
    return "\n".join(dict.fromkeys(out)) if out else None


def provenance_for(excerpt: str | None) -> dict[str, Any]:
    lowered = (excerpt or "").lower()
    markers = sorted(m for m in THIRD_PARTY_MARKERS if m in lowered)
    return {
        "record_text_near_file": excerpt,
        "third_party_origin_indicated": bool(markers),
        "third_party_markers_found": markers,
        # No file in this record has documented upstream sources or a file-level licence, so a
        # human must review provenance before reuse, whatever the keyword check finds.
        "review_required": True,
        "review_note": (
            "Record text indicates a possible third-party origin or an external input; upstream "
            "terms are not stated in the Zenodo metadata."
            if markers
            else "The record does not mention this file; origin unknown."
            if excerpt is None
            else "Record text names no third-party origin; upstream inputs and terms are still "
            "not documented. Absence of a keyword is not confirmation of provenance."
        ),
    }


def licence_for(record_licence: dict | None) -> dict[str, Any]:
    lic_id = (record_licence or {}).get("id")
    return {
        "record_level_licence_id": lic_id,
        "record_level_licence_url": (
            "https://creativecommons.org/licenses/by/4.0/" if lic_id == "cc-by-4.0" else None
        ),
        "licence_url_note": "Derived from the licence id; Zenodo metadata supplies the id only.",
        "file_specific_licence": None,
        "file_specific_note": "Zenodo metadata states no file-specific licence for this file.",
        "attribution_required": True if lic_id and lic_id.startswith("cc-by") else None,
    }


def build_record_section(
    record: dict, *, record_url: str, api_url: str, retrieved_at: str
) -> dict[str, Any]:
    meta = record.get("metadata", {})
    description_text = strip_html(meta.get("description"))
    return {
        "record_id": record.get("id"),
        "doi": record.get("doi"),
        "concept_doi": record.get("conceptdoi"),
        "title": meta.get("title") or record.get("title"),
        "publication_date": meta.get("publication_date"),
        "version": meta.get("version"),
        "version_note": None if meta.get("version") else "Not provided by Zenodo.",
        "created": record.get("created"),
        "updated": record.get("updated"),
        "record_url": record_url,
        "api_url": api_url,
        "doi_url": record.get("doi_url"),
        "retrieved_at_utc": retrieved_at,
        "access_right": meta.get("access_right"),
        "resource_type": (meta.get("resource_type") or {}).get("type"),
        "licence": meta.get("license"),
        "creators": meta.get("creators"),
        "related_identifiers": meta.get("related_identifiers"),
        "description_text": description_text,
    }


def build_file_entry(
    *,
    filename: str,
    size_bytes: int,
    algo: str,
    expected_digest: str,
    download_url: str,
    local_path: str,
    status: str,
    calculated: dict[str, str] | None,
    downloaded_at: str | None,
    verified_at: str | None,
    error: str | None,
    record_licence: dict | None,
    description_text: str,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    ext = Path(filename).suffix.lower().lstrip(".")
    calc = calculated or {}
    return {
        "filename": filename,
        "extension": ext or None,
        "size_bytes": size_bytes,
        "zenodo_checksum": {"algorithm": algo, "value": expected_digest},
        "calculated_checksums": calc or None,
        "checksum_verified": bool(calc) and calc.get(algo) == expected_digest,
        "download_url": download_url,
        "local_path": local_path,
        "download_status": status,
        "downloaded_at_utc": downloaded_at,
        "verified_at_utc": verified_at,
        "error": error,
        "notes": notes or [],
        "licence": licence_for(record_licence),
        "provenance": provenance_for(description_excerpt(description_text, filename)),
    }


def load_manifest(path: Path) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def write_manifest_atomic(path: Path, manifest: dict) -> None:
    """Write JSON via a temp file + rename so a crash can never leave a half-written manifest."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
