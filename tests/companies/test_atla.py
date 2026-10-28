"""Offline tests for the ATLA (Japan) adapter and the stdlib xlsx reader."""
import io
import json
import unittest
import zipfile
from xml.sax.saxutils import escape

from collectors.companies import pipeline
from collectors.companies.normalize import name_norm
from collectors.companies.sources import atla, xlsx
from collectors.companies.store import SqliteStore
from tests.companies.helpers import fixture

FX = fixture("atla_rows.json")


def build_xlsx(rows, macros=False):
    """rows: list of {col: text}; written as inline strings (row 1 title, row 2 header, row 3 sub-header)."""
    sheet = ['<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/'
             'spreadsheetml/2006/main"><sheetData>']
    for i, row in enumerate(rows, 1):
        cells = "".join(f'<c r="{c}{i}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for c, v in row.items() if v)
        sheet.append(f'<row r="{i}">{cells}</row>')
    sheet.append("</sheetData></worksheet>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", "".join(sheet))
        if macros:
            z.writestr("xl/vbaProject.bin", b"\x00")
    return buf.getvalue()


def sheet_bytes(data_rows):
    return build_xlsx([{"A": "title"}, {"A": "物品役務等の名称", "F": "契約相手方"}, {}] + data_rows)


INDEX_HTML = ('<a href="kohyo_r08/08_kyoso-04.xlsx">4月</a><a href="kohyo_r07/07_zuikei-03.xlsx">3月</a>'
              '<a href="kohyo_r07/07_zuikei-04.xlsx">4月</a><a href="kohyo_r06/06_zuikei-04.xlsx">old</a>'
              '<a href="kohyo_r08/08_tokutei-04.xlsx">other</a>')
SOLE = [r for r in FX["rows"] if "燃料" not in r["A"]]
FUEL = [r for r in FX["rows"] if "燃料" in r["A"]]


class FakeFiles:
    def __init__(self, files):
        self.files, self.calls = files, []

    def get_bytes(self, url, accept="*/*"):
        self.calls.append(url)
        if url == atla.INDEX:
            return INDEX_HTML.encode()
        return self.files.get(url.rsplit("/", 2)[-2] + "/" + url.rsplit("/", 1)[-1])


def http():
    return FakeFiles({"kohyo_r07/07_zuikei-04.xlsx": sheet_bytes(SOLE[:2]),
                      "kohyo_r07/07_zuikei-03.xlsx": sheet_bytes(SOLE),
                      "kohyo_r08/08_kyoso-04.xlsx": sheet_bytes(FUEL)})


class TestXlsx(unittest.TestCase):
    def test_reads_inline_and_refuses_macros(self):
        self.assertEqual(xlsx.rows(build_xlsx([{"A": "x", "C": "y"}])), [{"A": "x", "C": "y"}])
        with self.assertRaises(xlsx.XlsxError):
            xlsx.rows(build_xlsx([{"A": "x"}], macros=True))
        with self.assertRaises(xlsx.XlsxError):
            xlsx.rows(b"not a zip")


class TestAtlaParse(unittest.TestCase):
    def test_file_order_and_kinds(self):
        self.assertEqual(atla.list_files(INDEX_HTML), ["kohyo_r06/06_zuikei-04.xlsx", "kohyo_r07/07_zuikei-04.xlsx",
                                                      "kohyo_r07/07_zuikei-03.xlsx", "kohyo_r08/08_kyoso-04.xlsx"])

    def test_split_name_address(self):
        self.assertEqual(atla.split_contractor("三菱重工業株式会社\n東京都千代田区"), ("三菱重工業株式会社", True))
        self.assertEqual(atla.split_contractor("Ｂｅｌｌ　Ｔｅｘｔｒｏｎ株式会社\n東京都渋谷区"), ("Bell Textron株式会社", True))
        self.assertEqual(atla.split_contractor("米海軍省"), ("米海軍省", False))
        self.assertEqual(atla.split_contractor("Oakwood Controls Corporation\n765 Foxtail Drive,York,PA,17404"),
                         ("Oakwood Controls Corporation", False))  # real foreign contractor: not JP
        self.assertEqual(atla.split_contractor("株式会社かまや 千葉県船橋市"), ("株式会社かまや", True))

    def test_us_agencies_are_governments(self):
        row = {"A": "部品", "F": "米国防脅威削減局"}
        self.assertIsNone(atla.Awards.parse(row, "kohyo_r07/07_zuikei-03.xlsx", 9))

    def test_prime_record(self):
        mhi = next(r for r in SOLE if "三菱重工業" in r["F"])
        rec = atla.Awards.parse(mhi, "kohyo_r07/07_zuikei-03.xlsx", 4)
        m = rec.mentions[0]
        self.assertEqual((m.name, m.country, m.identifiers), ("三菱重工業株式会社", "JP", [("JP_CORP", "8010401050387")]))
        self.assertEqual((m.signals[0].type, m.signals[0].layer, m.signals[0].date[:4]), ("contract", "A", "2026"))
        self.assertNotIn("[REDACTED]", json.dumps(rec.payload, ensure_ascii=False))  # officer column is never kept
        self.assertNotIn("千代田区", json.dumps(rec.payload, ensure_ascii=False))     # nor the address
        self.assertEqual(rec.payload["kind"], "sole_source")

    def test_skips_government_and_civil(self):
        navy = next(r for r in FX["rows"] if "米海軍省" in r["F"])
        self.assertIsNone(atla.Awards.parse(navy, "kohyo_r07/07_zuikei-03.xlsx", 5))
        self.assertIsNone(atla.Awards.parse(FUEL[0], "kohyo_r08/08_kyoso-04.xlsx", 4))

    def test_trading_house_is_intermediary(self):
        sojitz = next(r for r in SOLE if "双日" in r["F"])
        m = atla.Awards.parse(sojitz, "kohyo_r07/07_zuikei-03.xlsx", 6).mentions[0]
        self.assertEqual((m.supply_role, m.flags), ("SERVICES", ["TRADING_INTERMEDIARY"]))

    def test_japanese_legal_forms_are_equivalent(self):
        self.assertEqual(name_norm("株式会社ＩＨＩ"), name_norm("IHI Corporation"))
        self.assertEqual(name_norm("三菱重工業株式会社"), name_norm("三菱重工業"))
        self.assertNotEqual(name_norm("株式会社ＩＨＩエアロスペース"), name_norm("株式会社ＩＨＩ"))


class TestAtlaFetch(unittest.TestCase):
    def test_cursor_resumes_inside_and_across_files(self):
        adapter = atla.Awards()
        h = http()
        recs1, cur1 = adapter.fetch(h, None, 3)
        self.assertEqual(len(recs1), 2 + 1)  # 2 from 07_zuikei-04, then 1 from 07_zuikei-03 (r06 is before from_year)
        self.assertTrue(cur1.startswith("kohyo_r07/07_zuikei-03.xlsx|"))
        recs2, cur2 = adapter.fetch(h, cur1, 50)
        keys = [r.source_key for r in recs1 + recs2]
        self.assertEqual(len(keys), len(set(keys)))  # nothing read twice
        self.assertEqual(cur2, "kohyo_r08/08_kyoso-04.xlsx|END")
        self.assertFalse(any("08_kyoso" in k for k in keys))  # the fuel-only file gives no records
        recs3, cur3 = adapter.fetch(h, cur2, 50)
        self.assertEqual((recs3, cur3), ([], cur2))  # nothing new until ATLA publishes a new month

    def test_pipeline_end_to_end(self):
        store = SqliteStore()
        res = pipeline.run_source(store, atla.Awards(kinds=("zuikei",)), http(), 50)
        self.assertEqual(res["status"], "ok")
        names = {r["name"] for r in store.query("SELECT name FROM company")}
        self.assertIn("三菱重工業株式会社", names)
        self.assertNotIn("米海軍省", names)
        self.assertEqual({r["country"] for r in store.query("SELECT country FROM company")}, {"JP"})
        self.assertEqual({r["layer"] for r in store.query("SELECT layer FROM company")}, {"A"})
        # same corporate number in two monthly files -> one company (matched by JP_CORP)
        self.assertEqual(store.query("SELECT COUNT(*) n FROM company WHERE name='三菱重工業株式会社'")[0]["n"], 1)
        again = pipeline.run_source(store, atla.Awards(kinds=("zuikei",)), http(), 50)
        self.assertEqual(again["new_observations"], 0)


if __name__ == "__main__":
    unittest.main()
