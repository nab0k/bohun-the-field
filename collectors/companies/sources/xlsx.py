"""Minimal read-only xlsx reader (stdlib zipfile + XML). Values only: no formulas, no macros, no styles.

Used for government spreadsheets (ATLA). A workbook with a VBA project is refused.
"""
import io
import re
import xml.etree.ElementTree as ET
import zipfile

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_COL = re.compile(r"[A-Z]+")
MAX_UNCOMPRESSED = 50 * 1024 * 1024


class XlsxError(ValueError):
    pass


def _text(node):
    return "".join(t.text or "" for t in node.iter(_NS + "t"))


def rows(data, sheet=1):
    """Return a list of {column_letter: value-as-string} for every row of the given sheet (1-based)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise XlsxError("not an xlsx file") from err
    names = zf.namelist()
    if any("vbaProject" in n for n in names):
        raise XlsxError("workbook with macros refused")
    if sum(i.file_size for i in zf.infolist()) > MAX_UNCOMPRESSED:
        raise XlsxError("xlsx too large when unpacked")
    shared = []
    if "xl/sharedStrings.xml" in names:
        shared = [_text(si) for si in ET.fromstring(zf.read("xl/sharedStrings.xml")).findall(_NS + "si")]
    path = f"xl/worksheets/sheet{sheet}.xml"
    if path not in names:
        raise XlsxError(f"no {path}")
    out = []
    for row in ET.fromstring(zf.read(path)).iter(_NS + "row"):
        cells = {}
        for c in row.findall(_NS + "c"):
            col = _COL.match(c.get("r") or "")
            if not col:
                continue
            kind, v = c.get("t"), c.find(_NS + "v")
            if kind == "inlineStr":
                node = c.find(_NS + "is")
                value = _text(node) if node is not None else ""
            elif v is None:
                continue
            elif kind == "s":
                value = shared[int(v.text)]
            else:
                value = v.text or ""
            cells[col.group()] = value
        out.append(cells)
    return out
