"""Offline tests: lead -> card rule (D02) and benchmark completeness."""
import json
import pathlib
import unittest

from collectors.companies import cards, pipeline, report
from collectors.companies.model import Mention, Record, SignalIn
from collectors.companies.pg_store import PgStore
from collectors.companies.store import SqliteStore
from tests.companies.test_pg_store import SqliteAsPg

BENCH = pathlib.Path(__file__).resolve().parents[2] / "collectors" / "companies" / "benchmarks"


class Static:
    def __init__(self, records):
        self.source, self.records = "s", records

    def fetch(self, http, cursor, limit):
        return self.records, None


def load(store, *mentions):
    recs = [Record("s", str(i), {"i": i, "n": m.name}, [m]) for i, m in enumerate(mentions)]
    pipeline.run_source(store, Static(recs), None, 100)


def status(store, name):
    return store.query("SELECT status FROM company WHERE name=?", (name,))[0]["status"]


class TestCards(unittest.TestCase):
    def setUp(self):
        self.store = SqliteStore()
        load(self.store,
             Mention("Contract Co", "DE", identifiers=[("LEI", "L1")], signals=[SignalIn("contract")]),
             Mention("No Registry Co", "DE", identifiers=[("TED_ORG", "uuid-1")], signals=[SignalIn("tender_award")]),
             Mention("Police Supplier", "DE", identifiers=[("NATIONAL:DE", "HRB1")],
                     signals=[SignalIn("tender_award", layer="C")]),
             Mention("Legacy Lead", "FI", notion_page_id="a" * 32,
                     signals=[SignalIn("association", layer="D"), SignalIn("legacy_assessment", layer="A")]),
             Mention("Ministry X", "BE", identifiers=[("EU_PIC", "1")], flags=["ORG_TYPE:PUB"],
                     signals=[SignalIn("grant")]),
             Mention("Registry Lab", "NL", identifiers=[("NATIONAL:NL", "123")], signals=[SignalIn("ncage")]),
             Mention("Red Corp", "CN", identifiers=[("LEI", "L2")], signals=[SignalIn("contract")]))

    def test_rule(self):
        out = cards.promote(self.store)
        self.assertEqual(out, {"promoted": 2, "demoted": 0, "cards": 2})
        self.assertEqual(status(self.store, "Contract Co"), "card")      # registry id + layer A
        self.assertEqual(status(self.store, "Registry Lab"), "card")     # national number + layer B
        self.assertEqual(status(self.store, "No Registry Co"), "candidate")  # TED UUID is not a register
        self.assertEqual(status(self.store, "Police Supplier"), "candidate")  # layer C only
        self.assertEqual(status(self.store, "Legacy Lead"), "candidate")  # old assessment does not count
        self.assertEqual(status(self.store, "Ministry X"), "candidate")  # public body
        self.assertEqual(status(self.store, "Red Corp"), "excluded")      # never touched

    def test_idempotent_and_demotes(self):
        cards.promote(self.store)
        self.assertEqual(cards.promote(self.store), {"promoted": 0, "demoted": 0, "cards": 2})
        cid = self.store.query("SELECT id FROM company WHERE name='Registry Lab'")[0]["id"]
        self.store.db.execute("DELETE FROM signal WHERE company_id=?", (cid,))
        self.assertEqual(cards.promote(self.store)["demoted"], 1)
        self.assertEqual(status(self.store, "Registry Lab"), "candidate")

    def test_sql_goes_through_pg_translation(self):
        conn = SqliteAsPg()
        pg = PgStore(conn)
        load(pg, Mention("Contract Co", "DE", identifiers=[("LEI", "L1")], signals=[SignalIn("contract")]))
        self.assertEqual(cards.promote(pg)["cards"], 1)
        self.assertTrue(all("%" not in s.replace("%s", "") for s in conn.statements))


class TestCompleteness(unittest.TestCase):
    def test_ids_names_transnational_and_ru_cn(self):
        store = SqliteStore()
        load(store,
             Mention("LOCKHEED MARTIN CORPORATION", "US", identifiers=[("UEI", "U1")], signals=[SignalIn("contract")]),
             Mention("Airbus SE", "NL", identifiers=[("LEI", "A1")], signals=[SignalIn("tender_award")]),
             Mention("Rostec", "RU", signals=[SignalIn("news")]))
        bench = [{"name": "Lockheed Martin Corp.", "country": "US"},
                 {"name": "Airbus", "country": None},
                 {"name": "Thales", "country": "FR"},
                 {"name": "Rostec", "country": "RU", "expected": "excluded_ru_cn"},
                 {"name": "AVIC", "country": "CN", "expected": "excluded_ru_cn"}]
        out = report.completeness(store, bench)
        self.assertEqual((out["total"], out["covered"], out["missing"]), (3, 2, ["Thales"]))
        self.assertEqual(out["ru_cn_check"], {"absent": 1, "excluded": 1, "not_excluded": []})

    def test_bundled_benchmarks_are_valid(self):
        for name in ("sipri_top100_2024.json", "dod_top_recipients_fy2025.json"):
            data = json.loads((BENCH / name).read_text(encoding="utf-8"))
            self.assertTrue(data["_source"])
            self.assertTrue(all(e.get("name") for e in data["entries"]))
        sipri = json.loads((BENCH / "sipri_top100_2024.json").read_text(encoding="utf-8"))["entries"]
        self.assertEqual(sum(1 for e in sipri if e.get("expected") == "excluded_ru_cn"), 10)


if __name__ == "__main__":
    unittest.main()
