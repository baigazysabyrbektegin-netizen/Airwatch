"""Build tiny synthetic .xlsx files (zip + XML) for tests; no spreadsheet library needed.

Cell values in a row (index 0 = column A): None = no cell, str = text, int/float = number written
with ``repr`` (so digits are exact), ("f", formula, cached_text) = formula cell with cached value.
An empty row ``[]`` is written as a blank styled row like the real workbook's trailing rows.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def col_letter(i: int) -> str:
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def make_xlsx(path: Path, sheets: dict[str, list[list]], *, shared: bool = True, hidden: tuple[str, ...] = (),
              tables: dict[str, str] | None = None, creator: str | None = None) -> Path:
    shared_list: list[str] = []

    def sidx(text: str) -> int:
        if text not in shared_list:
            shared_list.append(text)
        return shared_list.index(text)

    sheet_xml = []
    for name, rows in sheets.items():
        maxc = max((len(r) for r in rows), default=1)
        out = [f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}" xmlns:r="{RNS}">',
               f'<dimension ref="A1:{col_letter(maxc - 1)}{len(rows)}"/><sheetData>']
        for rn, row in enumerate(rows, start=1):
            if not row:
                out.append(f'<row r="{rn}"><c r="A{rn}" s="1"/></row>')
                continue
            cells = []
            for ci, v in enumerate(row):
                ref = f"{col_letter(ci)}{rn}"
                if v is None:
                    continue
                if isinstance(v, tuple) and v[0] == "n":   # numeric cell with exact stored text, as Excel writes it
                    cells.append(f'<c r="{ref}"><v>{escape(v[1])}</v></c>')
                elif isinstance(v, tuple) and v[0] == "f":
                    cells.append(f'<c r="{ref}" t="str"><f>{escape(v[1])}</f><v>{escape(str(v[2]))}</v></c>')
                elif isinstance(v, str):
                    if shared:
                        cells.append(f'<c r="{ref}" t="s"><v>{sidx(v)}</v></c>')
                    else:
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{escape(v)}</t></is></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{v!r}</v></c>')
            out.append(f'<row r="{rn}">{"".join(cells)}</row>')
        out.append("</sheetData></worksheet>")
        sheet_xml.append("".join(out))

    wb_sheets = "".join(
        f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"' + (' state="hidden"' if n in hidden else "") + "/>"
        for i, n in enumerate(sheets, start=1)
    )
    workbook = f'<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="{NS}" xmlns:r="{RNS}"><sheets>{wb_sheets}</sheets></workbook>'
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(sheets) + 1)
    )
    rels_xml = f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", rels_xml)
        for i, x in enumerate(sheet_xml, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", x)
        if shared_list:
            z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst xmlns="{NS}">' + "".join(f"<si><t>{escape(s)}</t></si>" for s in shared_list) + "</sst>")
        for n, ref in (tables or {}).items():
            z.writestr(f"xl/tables/{n}.xml", f'<?xml version="1.0"?><table xmlns="{NS}" id="1" name="{n}" displayName="{n}" ref="{ref}"/>')
        if creator:
            z.writestr("docProps/core.xml", f'<?xml version="1.0"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>{escape(creator)}</dc:creator></cp:coreProperties>')
    return path


DATA_HEADER = ["Device ID", "PM25", "year", "month", "day", "hour", "Quadrant"]


def data_row(device, pm, y, m, d, h, quad="Q1"):
    return [device, pm, y, m, d, h, ("f", "INDEX(locations!$P$2:$P$9,MATCH('2021'!$A2,locations!$M$2:$M$9,0))", quad)]


def locations_rows(devs: list[tuple]) -> list[list]:
    """devs: (device, lat, lon[, lookup_quadrant_label_or_None]). Column layout follows the real sheet."""
    rows = [[None, "Device ID", "latitude", "longitude", "Q1", "Q2", "Q3", "Q4", None, None, None, None, None, None, "Quadrant", "Quadrant"]]
    for i, t in enumerate(devs, start=1):
        dev, lat, lon = t[0], t[1], t[2]
        lookup = t[3] if len(t) > 3 else None
        row: list = [i, dev, lat, lon, None, None, None, None, None, None, None, None]
        if lookup:
            row += [dev, i, None, lookup]  # M, N, O, P
        rows.append(row)
    return rows


def clarity_workbook(path: Path, records: list[list], devs: list[tuple], blank_rows: int = 0, **kw) -> Path:
    data = [DATA_HEADER] + records + [[] for _ in range(blank_rows)]
    return make_xlsx(path, {"2021": data, "locations": locations_rows(devs), "monthly": [[None], [None], [None], [None], [None, "JAN"]]},
                     tables={"Table1": f"A1:G{len(records) + 1}"}, **kw)
