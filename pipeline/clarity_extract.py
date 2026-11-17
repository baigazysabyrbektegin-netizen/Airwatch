"""Read the Clarity workbook (read-only), verify it against the Zenodo manifest and write the raw sheet
contents, unchanged, to data/interim/clarity/extracted/.

Nothing is cleaned here. Cell values are the text stored in the workbook; formula cells contribute their
cached value (the ``Quadrant`` column of the data sheet is such a formula; see the inventory).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pandas as pd

from pipeline import manifest as mf
from pipeline.xlsx_reader import XlsxWorkbook

DATA_SHEET = "2021"
LOCATIONS_SHEET = "locations"
DATA_COLUMNS = {"A": "Device ID", "B": "PM25", "C": "year", "D": "month", "E": "day", "F": "hour", "G": "Quadrant"}
EXPECTED_DATA_HEADER = list(DATA_COLUMNS.values())


class ClarityExtractError(Exception):
    pass


def verify_workbook(path: Path, expected_sha256: str | None, expected_md5: str | None) -> dict[str, str]:
    calc = mf.hash_file(path, ("md5", "sha256"))
    if expected_sha256 and calc["sha256"] != expected_sha256:
        raise ClarityExtractError(f"Workbook SHA-256 differs from the Zenodo manifest: {path}")
    if expected_md5 and calc["md5"] != expected_md5:
        raise ClarityExtractError(f"Workbook MD5 differs from the Zenodo manifest: {path}")
    return calc


def _safe_name(sheet: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", sheet)


def read_workbook(path: Path) -> dict:
    """Read every sheet. Returns a dict with sheet frames and structural information."""
    with XlsxWorkbook(path) as wb:
        inventory = []
        frames: dict[str, pd.DataFrame] = {}
        for s in wb.sheets:
            df, info = wb.read_grid(s.name)
            frames[s.name] = df
            inventory.append(
                {
                    "sheet": s.name, "state": s.state, "dimension": s.dimension,
                    "rows_in_file": info["rows_in_file"], "formula_cells": info["formula_cells"],
                    "columns_with_values": info["columns_with_values"],
                    "non_empty_cells": int(df.drop(columns="excel_row").notna().sum().sum()),
                }
            )
        if DATA_SHEET not in frames or LOCATIONS_SHEET not in frames:
            raise ClarityExtractError(f"Workbook must contain sheets {DATA_SHEET!r} and {LOCATIONS_SHEET!r}; found {list(frames)}")
        data = frames[DATA_SHEET]
        has_header_row = (data["excel_row"] == 1).any()
        header = {c: (data.loc[data["excel_row"] == 1, c].iloc[0] if has_header_row and c in data.columns else None) for c in DATA_COLUMNS}
        if list(header.values()) != EXPECTED_DATA_HEADER:
            raise ClarityExtractError(f"Unexpected header in sheet {DATA_SHEET!r}: {list(header.values())}")
        data = data[data["excel_row"] > 1].rename(columns=DATA_COLUMNS).reset_index(drop=True)
        for col in DATA_COLUMNS.values():  # a column with no value at all would be missing from the grid
            if col not in data.columns:
                data[col] = pd.Series([pd.NA] * len(data), dtype="str")
        frames[DATA_SHEET] = data[["excel_row"] + EXPECTED_DATA_HEADER]
        return {
            "frames": frames,
            "inventory": inventory,
            "tables": wb.tables(),
            "defined_names": wb.defined_names(),
            "doc_properties": wb.doc_properties(),
            "data_sheet_formulas": wb.first_formulas(DATA_SHEET, 20),
            "locations_formulas": wb.first_formulas(LOCATIONS_SHEET, 5),
        }


def _write_csv_exact(df: pd.DataFrame, path: Path) -> str:
    """Write a CSV. If the file exists it must be identical (never overwrite a different file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False, na_rep="", lineterminator="\n", encoding="utf-8")
        new = mf.hash_file(Path(tmp), ("sha256",))["sha256"]
        if path.exists():
            if mf.hash_file(path, ("sha256",))["sha256"] != new:
                raise ClarityExtractError(
                    f"{path} exists with different content; refusing to overwrite. Move it away to re-extract."
                )
            os.unlink(tmp)
            return "already_extracted_verified"
        os.replace(tmp, path)
        return "extracted"
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_extracted(frames: dict[str, pd.DataFrame], out_dir: Path) -> list[dict]:
    records = []
    for name, df in frames.items():
        p = out_dir / f"sheet_{_safe_name(name)}.csv"
        status = _write_csv_exact(df, p)
        records.append(
            {"sheet": name, "filename": p.name, "rows": int(len(df)), "size_bytes": p.stat().st_size,
             "sha256": mf.hash_file(p, ("sha256",))["sha256"], "status": status, "local_path": p.as_posix()}
        )
    return records


def write_inventory_json(info: dict, path: Path) -> None:
    payload = {k: v for k, v in info.items() if k != "frames"}
    mf.write_manifest_atomic(path, payload)


def read_extracted_data_sheet(extracted_dir: Path) -> pd.DataFrame:
    """Load the extracted data sheet back as text (used by tests and by --skip-extract)."""
    df = pd.read_csv(extracted_dir / f"sheet_{DATA_SHEET}.csv", dtype=str, keep_default_na=False, na_values=[""])
    df["excel_row"] = df["excel_row"].astype("int64")
    return df
