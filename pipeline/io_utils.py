"""Output helpers shared by the spatial (Stage 4) pipeline.

Every write goes through a temp file. An existing file whose content would change is never replaced
unless the caller explicitly allows it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from pipeline import manifest as mf

REPO_ROOT = Path(__file__).resolve().parents[1]


class OutputExistsError(Exception):
    pass


def rel(p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return Path(p).as_posix()


def guarded_write(path: Path, writer: Callable[[Path], None], allow_overwrite: bool = False) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    tmp_p = Path(tmp)
    try:
        writer(tmp_p)
        if path.exists():
            if mf.hash_file(path, ("sha256",))["sha256"] == mf.hash_file(tmp_p, ("sha256",))["sha256"]:
                tmp_p.unlink()
                return "unchanged"
            if not allow_overwrite:
                raise OutputExistsError(
                    f"{path} already exists with different content and was not overwritten. "
                    "Re-run with --allow-overwrite if the change is intended."
                )
            os.replace(tmp_p, path)
            return "overwritten_with_approval"
        os.replace(tmp_p, path)
        return "written"
    except BaseException:
        tmp_p.unlink(missing_ok=True)
        raise


def _with_metadata(table: pa.Table, metadata: dict | None) -> pa.Table:
    md = dict(table.schema.metadata or {})
    if metadata is not None:
        md[b"airwatch.processing"] = json.dumps(metadata, sort_keys=True, default=str).encode("utf-8")
    return table.replace_schema_metadata(md)


def write_parquet(df: pd.DataFrame, path: Path, metadata: dict | None = None, allow_overwrite: bool = False) -> str:
    def w(p: Path):
        pq.write_table(_with_metadata(pa.Table.from_pandas(df, preserve_index=False), metadata), p, compression="snappy")

    return guarded_write(path, w, allow_overwrite)


def write_geoparquet(gdf, path: Path, metadata: dict | None = None, allow_overwrite: bool = False) -> str:
    """GeoParquet (geometry column, CRS and bbox in the 'geo' metadata) plus our provenance metadata."""
    def w(p: Path):
        gdf.to_parquet(p, index=False, compression="snappy")
        table = pq.read_table(p)
        pq.write_table(_with_metadata(table, metadata), p, compression="snappy")

    return guarded_write(path, w, allow_overwrite)


def write_geojson(gdf, path: Path, allow_overwrite: bool = False) -> str:
    # The layer name is written into the file; use the final file's stem so the temp file's random name never leaks in.
    layer = Path(path).stem
    return guarded_write(path, lambda p: gdf.to_file(p, driver="GeoJSON", layer=layer, COORDINATE_PRECISION=6), allow_overwrite)


def write_text(text: str, path: Path, allow_overwrite: bool = False) -> str:
    return guarded_write(path, lambda p: p.write_text(text, encoding="utf-8", newline="\n"), allow_overwrite)


def output_record(path: Path, fmt: str, role: str, rows, status: str) -> dict:
    return {"path": rel(path), "format": fmt, "role": role, "rows": rows, "size_bytes": Path(path).stat().st_size,
            "sha256": mf.hash_file(Path(path), ("sha256",))["sha256"], "write_status": status}
