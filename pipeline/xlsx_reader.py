"""Minimal, dependency-free XLSX reader (zipfile + XML) that keeps each cell's stored text exactly.

Why not openpyxl/pandas.read_excel? They are not installed, and they convert cells to Python floats.
Here a numeric cell such as ``17.940000000000001`` stays exactly as stored in the workbook.
Only what the project needs is supported: sheet listing, cell text, formulas flagged, document properties.
The workbook is opened read-only and never modified.
"""

from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

NS = {
    "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "rel": "http://schemas.openxmlformats.org/package/2006/relationships",
}
M = "{%s}" % NS["m"]
R = "{%s}" % NS["r"]
_REF = re.compile(r"^([A-Z]+)(\d+)$")


class XlsxError(Exception):
    pass


@dataclass(frozen=True)
class SheetInfo:
    name: str
    path: str          # path inside the zip, e.g. xl/worksheets/sheet1.xml
    state: str         # visible / hidden / veryHidden
    dimension: str | None


def col_to_index(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


class XlsxWorkbook:
    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self._zip = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as exc:
            raise XlsxError(f"{self.path} is not a valid XLSX (zip) file") from exc
        self._shared: list[str] | None = None
        self.sheets = self._read_sheets()

    def close(self) -> None:
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------------ structure
    def _read_sheets(self) -> list[SheetInfo]:
        try:
            wb = ET.fromstring(self._zip.read("xl/workbook.xml"))
            rels = ET.fromstring(self._zip.read("xl/_rels/workbook.xml.rels"))
        except KeyError as exc:
            raise XlsxError(f"Missing workbook part: {exc}") from exc
        targets = {r.get("Id"): r.get("Target") for r in rels}
        out = []
        for s in wb.find(M + "sheets"):
            tgt = targets[s.get(R + "id")]
            path = tgt.lstrip("/") if tgt.startswith("/") else "xl/" + tgt
            dim = None
            with self._zip.open(path) as fh:  # the <dimension> element is near the top
                head = fh.read(4000).decode("utf-8", "replace")
            m = re.search(r'<dimension ref="([^"]+)"', head)
            if m:
                dim = m.group(1)
            out.append(SheetInfo(s.get("name"), path, s.get("state", "visible"), dim))
        return out

    def sheet(self, name: str) -> SheetInfo:
        for s in self.sheets:
            if s.name == name:
                return s
        raise XlsxError(f"Sheet {name!r} not found. Available: {[s.name for s in self.sheets]}")

    def part_names(self) -> list[str]:
        return self._zip.namelist()

    def defined_names(self) -> list[dict]:
        wb = ET.fromstring(self._zip.read("xl/workbook.xml"))
        dn = wb.find(M + "definedNames")
        return [] if dn is None else [{"name": d.get("name"), "ref": d.text} for d in dn]

    def doc_properties(self) -> dict[str, str]:
        props: dict[str, str] = {}
        for part in ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml"):
            if part in self._zip.namelist():
                root = ET.fromstring(self._zip.read(part))
                for el in root.iter():
                    tag = el.tag.split("}")[-1]
                    if el.text and el.text.strip() and len(list(el)) == 0:
                        props[f"{part.split('/')[-1]}:{tag}"] = el.text.strip()
        return props

    # ------------------------------------------------------------------ cells
    def _shared_strings(self) -> list[str]:
        if self._shared is None:
            self._shared = []
            if "xl/sharedStrings.xml" in self._zip.namelist():
                root = ET.fromstring(self._zip.read("xl/sharedStrings.xml"))
                for si in root:
                    # concatenate text runs, ignoring phonetic (rPh) text
                    parts = []
                    for node in si.iter():
                        if node.tag == M + "t":
                            parts.append(node.text or "")
                        elif node.tag == M + "rPh":
                            for t in node.iter(M + "t"):
                                t.text = ""
                    self._shared.append("".join(parts))
        return self._shared

    def iter_rows(self, sheet_name: str) -> Iterator[tuple[int, dict[str, str], int]]:
        """Yield (row_number, {column_letters: cell_text}, n_formula_cells). Empty cells are omitted."""
        shared = self._shared_strings()
        info = self.sheet(sheet_name)
        with self._zip.open(info.path) as fh:
            for _, el in ET.iterparse(fh, events=("end",)):
                if el.tag != M + "row":
                    continue
                cells: dict[str, str] = {}
                n_formula = 0
                for c in el:
                    ref = _REF.match(c.get("r", ""))
                    if not ref:
                        continue
                    col = ref.group(1)
                    t = c.get("t")
                    if c.find(M + "f") is not None:
                        n_formula += 1
                    if t == "inlineStr":
                        is_ = c.find(M + "is")
                        val = "".join(x.text or "" for x in is_.iter(M + "t")) if is_ is not None else None
                    else:
                        v = c.find(M + "v")
                        val = v.text if v is not None else None
                        if t == "s" and val is not None:
                            val = shared[int(val)]
                    if val is not None and val != "":
                        cells[col] = val
                yield int(el.get("r")), cells, n_formula
                el.clear()

    def read_grid(self, sheet_name: str) -> tuple[pd.DataFrame, dict]:
        """Read a whole sheet as text, one column per Excel column letter (``A``, ``B`` ...).

        Absent/empty cells are NaN. ``excel_row`` holds the worksheet row number. Rows that exist in the
        file but contain no values are kept (they are reported as blank by callers).
        Returns (DataFrame, info) where info has row/formula counts."""
        rows, nums, formulas = [], [], 0
        for rnum, cells, nf in self.iter_rows(sheet_name):
            formulas += nf
            rows.append(cells)
            nums.append(rnum)
        letters = sorted({c for r in rows for c in r}, key=col_to_index)
        data = {c: [r.get(c, np.nan) for r in rows] for c in letters}
        df = pd.DataFrame(data, dtype="str")
        df.insert(0, "excel_row", np.array(nums, dtype="int64"))
        return df, {"rows_in_file": len(rows), "formula_cells": formulas, "columns_with_values": letters}

    def first_formulas(self, sheet_name: str, max_rows: int = 60) -> dict[str, str]:
        """First formula text found per column in the first ``max_rows`` rows (documentation only)."""
        found: dict[str, str] = {}
        info = self.sheet(sheet_name)
        n = 0
        with self._zip.open(info.path) as fh:
            for _, el in ET.iterparse(fh, events=("end",)):
                if el.tag != M + "row":
                    continue
                n += 1
                for c in el:
                    f = c.find(M + "f")
                    ref = _REF.match(c.get("r", ""))
                    if f is not None and f.text and ref and ref.group(1) not in found:
                        found[ref.group(1)] = f.text
                el.clear()
                if n >= max_rows:
                    break
        return found

    def tables(self) -> list[dict]:
        """Excel 'table' objects (name and declared cell range), e.g. the extent of the real data."""
        out = []
        for part in self._zip.namelist():
            if part.startswith("xl/tables/") and part.endswith(".xml"):
                root = ET.fromstring(self._zip.read(part))
                out.append({"part": part, "name": root.get("name"), "ref": root.get("ref")})
        return out
