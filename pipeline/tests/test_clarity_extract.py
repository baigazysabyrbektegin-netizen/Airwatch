import pandas as pd
import pytest

from pipeline import clarity_extract as ce
from pipeline import manifest as mf
from pipeline.tests.xlsx_helpers import DATA_HEADER, clarity_workbook, data_row, make_xlsx

DEVS = [("D1", "42.85", "74.60", "Q4"), ("D2", "42.90", "74.65", "Q1"), ("D3", "42.80", "74.50")]
RECS = [data_row("D1", ("n", "12.340000000000001"), 2021, 3, 1, 1, "Q4"), data_row("D2", 20, 2021, 3, 1, 24, "Q1")]


def wb_path(tmp_path, **kw):
    return clarity_workbook(tmp_path / "w.xlsx", RECS, DEVS, **kw)


def test_workbook_reading_and_sheet_inspection(tmp_path):
    wb = ce.read_workbook(wb_path(tmp_path, blank_rows=3))
    assert [s["sheet"] for s in wb["inventory"]] == ["2021", "locations", "monthly"]
    data = wb["frames"]["2021"]
    assert list(data.columns) == ["excel_row"] + DATA_HEADER
    assert len(data) == 2 + 3                                   # records + blank rows, header removed
    assert data.loc[0, "PM25"] == "12.340000000000001"          # exact stored text
    assert data.loc[1, "hour"] == "24" and data.loc[0, "Quadrant"] == "Q4"   # formula cached value
    assert wb["tables"][0]["ref"] == "A1:G3"
    assert wb["data_sheet_formulas"]["G"].startswith("INDEX(")
    blank = data.iloc[2:]
    assert blank[DATA_HEADER].isna().all().all()


def test_wrong_header_or_missing_sheet_is_an_error(tmp_path):
    p = make_xlsx(tmp_path / "a.xlsx", {"2021": [["Device", "PM"]], "locations": [["x"]]})
    with pytest.raises(ce.ClarityExtractError, match="Unexpected header"):
        ce.read_workbook(p)
    p2 = make_xlsx(tmp_path / "b.xlsx", {"2021": [DATA_HEADER]})
    with pytest.raises(ce.ClarityExtractError, match="must contain sheets"):
        ce.read_workbook(p2)


def test_verify_workbook_checksums(tmp_path):
    p = wb_path(tmp_path)
    good = mf.hash_file(p, ("md5", "sha256"))
    assert ce.verify_workbook(p, good["sha256"], good["md5"]) == good
    with pytest.raises(ce.ClarityExtractError, match="SHA-256"):
        ce.verify_workbook(p, "0" * 64, None)
    with pytest.raises(ce.ClarityExtractError, match="MD5"):
        ce.verify_workbook(p, None, "0" * 32)


def test_extracted_csvs_keep_cell_text_and_are_not_overwritten(tmp_path):
    p = wb_path(tmp_path)
    before = p.read_bytes()
    frames = ce.read_workbook(p)["frames"]
    out = tmp_path / "extracted"
    recs = ce.write_extracted(frames, out)
    assert {r["status"] for r in recs} == {"extracted"} and (out / "sheet_2021.csv").exists()
    back = ce.read_extracted_data_sheet(out)
    assert back.loc[0, "PM25"] == "12.340000000000001" and back["excel_row"].tolist() == [2, 3]
    assert {r["status"] for r in ce.write_extracted(frames, out)} == {"already_extracted_verified"}
    (out / "sheet_2021.csv").write_text("changed", encoding="utf-8")
    with pytest.raises(ce.ClarityExtractError, match="refusing to overwrite"):
        ce.write_extracted(frames, out)
    assert (out / "sheet_2021.csv").read_text(encoding="utf-8") == "changed"
    assert p.read_bytes() == before                      # the workbook itself is never touched


def test_safe_name():
    assert ce._safe_name("a b/c") == "a_b_c"
