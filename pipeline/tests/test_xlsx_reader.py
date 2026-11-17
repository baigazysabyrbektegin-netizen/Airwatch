import zipfile

import numpy as np
import pytest

from pipeline.tests.xlsx_helpers import make_xlsx
from pipeline.xlsx_reader import XlsxError, XlsxWorkbook, col_to_index


def test_col_to_index():
    assert [col_to_index(c) for c in ("A", "Z", "AA", "AB")] == [1, 26, 27, 28]


@pytest.mark.parametrize("shared", [True, False])
def test_reads_text_numbers_formulas_and_blank_rows(tmp_path, shared):
    p = make_xlsx(
        tmp_path / "w.xlsx",
        {"Data": [["Name", "Value", "Calc"], ["a", ("n", "17.940000000000001"), ("f", "B2*2", "35.88")], ["b", None, None], [], ["é", 3, None]]},
        shared=shared,
    )
    with XlsxWorkbook(p) as wb:
        df, info = wb.read_grid("Data")
    assert df["excel_row"].tolist() == [1, 2, 3, 4, 5]
    assert df.loc[1, "B"] == "17.940000000000001"        # stored digits are kept exactly
    assert df.loc[1, "C"] == "35.88" and info["formula_cells"] == 1
    assert df.loc[2, "A"] == "b" and np.isnan(df.loc[2, "B"])  # absent cell -> NaN, never ""/0
    assert df.loc[3, ["A", "B", "C"]].isna().all()         # blank row is kept, with no values
    assert df.loc[4, "A"] == "é"


def test_sheet_inventory_state_dimension_and_tables(tmp_path):
    p = make_xlsx(tmp_path / "w.xlsx", {"One": [["h"], ["x"]], "Two": [["h", "i"]]}, hidden=("Two",), tables={"Table1": "A1:G10"}, creator="Someone")
    with XlsxWorkbook(p) as wb:
        assert [(s.name, s.state, s.dimension) for s in wb.sheets] == [("One", "visible", "A1:A2"), ("Two", "hidden", "A1:B1")]
        assert wb.tables() == [{"part": "xl/tables/Table1.xml", "name": "Table1", "ref": "A1:G10"}]
        assert wb.doc_properties() == {"core.xml:creator": "Someone"}
        assert wb.defined_names() == []
        assert wb.first_formulas("One") == {}
        with pytest.raises(XlsxError, match="not found"):
            wb.sheet("Nope")


def test_first_formulas_reports_formula_text(tmp_path):
    p = make_xlsx(tmp_path / "w.xlsx", {"S": [["a", "b"], [1, ("f", "A2+1", "2")]]})
    with XlsxWorkbook(p) as wb:
        assert wb.first_formulas("S") == {"B": "A2+1"}


def test_not_a_workbook_and_read_only(tmp_path):
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(XlsxError, match="not a valid XLSX"):
        XlsxWorkbook(bad)
    p = make_xlsx(tmp_path / "w.xlsx", {"S": [["a"], ["b"]]})
    before = p.read_bytes()
    with XlsxWorkbook(p) as wb:
        wb.read_grid("S")
    assert p.read_bytes() == before and zipfile.is_zipfile(p)
