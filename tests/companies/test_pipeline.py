"""Offline tests: adapters on saved fixtures, resolve, RU/CN rule, layers, failures, report (EXP-012)."""
import io
import json
import pathlib
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout

from collectors.companies import ownership, pipeline, report
from collectors.companies.model import Mention, Record, SignalIn
from collectors.companies.sources import legacy_notion, prozorro, ted, usaspending
from collectors.companies.store import SqliteStore
from tests.companies.helpers import FakeGleif, FakeHttp, fixture

US_API = usaspending.API


def recipients_http():
    data = fixture("usaspending_recipients.json")
    last = dict(data, page_metadata={"hasNext": False})
    return FakeHttp(post={f"{US_API}/search/spending_by_category/recipient/": lambda url, body: last})


def subawards_http():
    data = fixture("usaspending_subawards.json")
    return FakeHttp(post={f"{US_API}/search/spending_by_award/": dict(data, page_metadata={"hasNext": False})})


def ted_http():
    data = fixture("ted_awards.json")
    return FakeHttp(post={ted.API: dict(data, iterationNextToken=None)})


def prozorro_http():
    data = fixture("prozorro.json")
    get = {f"{prozorro.API}/tenders?": dict(data["feed"], next_page={})}
    for tid, tender in data["tenders"].items():
        get[f"{prozorro.API}/tenders/{tid}"] = {"data": tender}
    return FakeHttp(get=get)


def companies(store, **where):
    sql = "SELECT * FROM company" + (" WHERE " + " AND ".join(f"{k}=?" for k in where) if where else "")
    return store.query(sql, tuple(where.values()))


def one(store, name):
    rows = store.query("SELECT * FROM company WHERE name=?", (name,))
    assert len(rows) == 1, (name, len(rows))
    return rows[0]


class StaticAdapter:
    def __init__(self, source, records):
        self.source, self.records = source, records

    def fetch(self, http, cursor, limit):
        return self.records, None


class TestAdapters(unittest.TestCase):
    def test_recipients_two_lockheed_ueis_stay_separate(self):
        store = SqliteStore()
        adapter = usaspending.Recipients(psc_groups=["14"])
        res = pipeline.run_source(store, adapter, recipients_http(), 10)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["new_companies"], 4)  # LM x2 (different UEI) is never auto-merged
        lm = store.query("SELECT id FROM company WHERE name='LOCKHEED MARTIN CORPORATION'")
        self.assertEqual(len(lm), 2)
        self.assertEqual(store.query("SELECT COUNT(*) n FROM match_candidate WHERE reason='same_name_needs_review'")[0]["n"], 1)
        cats = {(r["axis"], r["value"]) for r in store.query("SELECT axis, value FROM company_category")}
        self.assertIn(("capability", "MSL"), cats)
        self.assertEqual({r["layer"] for r in companies(store)}, {"A"})

    def test_recipients_psc_filter_and_cursor(self):
        http = recipients_http()
        _, cursor = usaspending.Recipients(psc_groups=["14", "AC"]).fetch(http, None, 4)
        body = http.calls[0][2]
        self.assertEqual(body["filters"]["psc_codes"], {"require": [["Product", "14"]]})
        self.assertEqual(cursor, "1:1")  # first group done, limit reached before the next one
        http2 = recipients_http()
        usaspending.Recipients(psc_groups=["AC"]).fetch(http2, None, 1)
        self.assertEqual(http2.calls[0][2]["filters"]["psc_codes"], {"require": [["Research and Development", "AC"]]})

    def test_subawards_link_to_prime_and_rnd_role(self):
        store = SqliteStore()
        pipeline.run_source(store, usaspending.Recipients(psc_groups=["14"]), recipients_http(), 10)
        res = pipeline.run_source(store, usaspending.Subawards(), subawards_http(), 10)
        self.assertEqual(res["status"], "ok")
        ng = one(store, "NORTHROP GRUMMAN SYSTEMS CORPORATION")
        self.assertEqual((ng["country"], ng["city"], ng["supply_role"]), ("US", "EL SEGUNDO", "RND"))
        rel = store.query("SELECT to_id FROM company_relation WHERE from_id=? AND relation='supplies'", (ng["id"],))
        lm_ids = {r["id"] for r in store.query("SELECT id FROM company WHERE name='LOCKHEED MARTIN CORPORATION'")}
        self.assertEqual(len(rel), 1)
        self.assertIn(rel[0]["to_id"], lm_ids)
        trio = one(store, "TRIO MANUFACTURING INC")
        self.assertIn("SUPPLIES_TO_UNRESOLVED", json.loads(trio["flags"]))

    def test_ted_keeps_only_defence_awards_with_winner(self):
        recs, cursor = ted.Awards().fetch(ted_http(), None, 10)
        self.assertIsNone(cursor)
        self.assertEqual([r.source_key for r in recs], ["599732-2026"])
        m = recs[0].mentions[0]
        self.assertEqual((m.name, m.country), ("Weibel Scientific A/S", "DK"))
        self.assertEqual(m.identifiers, [("TED_ORG", "2a41f7c5-8084-4171-bc7f-ae8378af6a38")])
        self.assertEqual(m.codes, [("CPV", "35700000")])
        self.assertEqual(m.signals[0].counterpart, "Federal Office for Defence Procurement, armasuisse")

    def test_ted_timeout_is_an_error(self):
        http = FakeHttp(post={ted.API: {"timedOut": True}})
        with self.assertRaises(RuntimeError):
            ted.Awards().fetch(http, None, 5)

    def test_prozorro_active_awards_only_and_no_address(self):
        store = SqliteStore()
        res = pipeline.run_source(store, prozorro.Awards(), prozorro_http(), 10)
        self.assertEqual(res["status"], "ok")
        rows = companies(store)
        self.assertEqual(len(rows), 1)
        c = rows[0]
        self.assertEqual((c["name"], c["country"], c["city"]), ("ТОВ СИНТЕТИЧНИЙ ВИРОБНИК БПЛА", "UA", None))
        self.assertEqual(store.identifiers_of(c["id"]), [("EDRPOU", "99999999")])
        payloads = " ".join(r["payload"] for r in store.query("SELECT payload FROM observation"))
        self.assertNotIn("must not be stored", payloads)
        self.assertNotIn("CANCELLED SUPPLIER", payloads)
        self.assertEqual(c["layer"], "A")


class TestIdempotencyAndResolve(unittest.TestCase):
    def test_rerun_same_data_adds_nothing(self):  # AC1
        store = SqliteStore()
        pipeline.run_source(store, ted.Awards(), ted_http(), 10)
        before = report.summary(store)
        store.save_source_state(ted.SOURCE, None, 0, False)
        res = pipeline.run_source(store, ted.Awards(), ted_http(), 10)
        after = report.summary(store)
        self.assertEqual(res["new_observations"], 0)
        for key in ("companies", "with_identifier", "match_candidates", "observations_by_source"):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(store.query("SELECT COUNT(*) n FROM signal")[0]["n"], 1)

    def test_identifier_match_across_sources(self):
        store = SqliteStore()
        rec1 = Record("s1", "1", {"a": 1}, [Mention(name="Acme Defence Ltd", country="GB", identifiers=[("LEI", "L1")])])
        rec2 = Record("s2", "1", {"a": 2}, [Mention(name="ACME DEFENCE LIMITED (UK)", country="GB",
                                                    identifiers=[("LEI", "L1")])])
        pipeline.run_source(store, StaticAdapter("s1", [rec1]), None, 5)
        res = pipeline.run_source(store, StaticAdapter("s2", [rec2]), None, 5)
        self.assertEqual(res["matched"], 1)
        self.assertEqual(len(companies(store)), 1)

    def test_identifier_conflict_goes_to_review(self):
        store = SqliteStore()
        a = Record("s", "a", {"a": 1}, [Mention(name="Alpha", country="DE", identifiers=[("LEI", "LA")])])
        b = Record("s", "b", {"b": 1}, [Mention(name="Beta", country="DE", identifiers=[("LEI", "LB")])])
        mixed = Record("s", "c", {"c": 1}, [Mention(name="Alpha", country="DE",
                                                    identifiers=[("LEI", "LA"), ("LEI", "LB")])])
        res = pipeline.run_source(store, StaticAdapter("s", [a, b, mixed]), None, 5)
        self.assertEqual((res["new_companies"], res["candidates"]), (2, 1))
        self.assertEqual(store.query("SELECT COUNT(*) n FROM match_candidate WHERE reason='identifier_conflict'")[0]["n"], 2)

    def test_same_name_other_country_not_merged(self):
        store = SqliteStore()
        recs = [Record("s", "1", {"x": 1}, [Mention(name="Nova Systems", country="AU")]),
                Record("s", "2", {"x": 2}, [Mention(name="Nova Systems", country="DE")])]
        pipeline.run_source(store, StaticAdapter("s", recs), None, 5)
        self.assertEqual(len(companies(store)), 2)

    def test_name_match_attaches_to_lead_without_identifiers(self):
        store = SqliteStore()
        recs = [Record("s", "1", {"x": 1}, [Mention(name="Nova Systems GmbH", country="DE")]),
                Record("s", "2", {"x": 2}, [Mention(name="NOVA SYSTEMS", country="DE",
                                                    identifiers=[("NATIONAL:DE", "HRB1")])])]
        res = pipeline.run_source(store, StaticAdapter("s", recs), None, 5)
        self.assertEqual((res["new_companies"], res["matched"]), (1, 1))


class TestLegacyImport(unittest.TestCase):
    ROWS = [
        {"url": "https://www.notion.so/Acme-" + "a" * 32, "Name": "Acme Optics", "Country": "Germany",
         "City": "Jena", "Website": "https://acme.example", "Source type": "company_site",
         "Defence evidence layer": "B_MARKET_DECLARED", "Flags": '["CHINA", "NO CITY"]', "Review status": "HOLD",
         "Product": None, "Signal": None},
        {"url": "https://www.notion.so/Acme-" + "b" * 32, "Name": "ACME OPTICS GMBH", "Country": "Germany",
         "Source type": "exhibition", "Defence evidence layer": "D_LEAD_ONLY", "Flags": "[]"},
        {"url": "https://www.notion.so/Kyiv-" + "c" * 32, "Name": "Kyiv Drones", "Country": "Ukraine",
         "City": "Kyiv", "Source type": "association", "Flags": "[]"},
        {"url": "https://www.notion.so/no-id", "Name": "Broken"},
    ]

    def test_ids_preserved_never_merged_and_layer_d(self):  # AC5
        store = SqliteStore()
        res = pipeline.run_source(store, legacy_notion.Legacy(self.ROWS), None, 10)
        self.assertEqual(res["status"], "ok")
        rows = companies(store)
        self.assertEqual(sorted(r["notion_page_id"] for r in rows), ["a" * 32, "b" * 32, "c" * 32])
        self.assertEqual({r["layer"] for r in rows}, {"D"})  # a legacy discovery frame never upgrades a lead
        self.assertEqual(store.query("SELECT COUNT(*) n FROM match_candidate WHERE reason='legacy_same_name'")[0]["n"], 1)
        acme = store.query("SELECT * FROM company WHERE notion_page_id=?", ("a" * 32,))[0]
        flags = json.loads(acme["flags"])
        self.assertIn("OWNERSHIP_LINK_CN", flags)
        self.assertIn("LEGACY_REVIEW:HOLD", flags)
        self.assertEqual(acme["status"], "candidate")  # partial CN link is shown, not excluded
        legacy = store.query("SELECT layer FROM signal WHERE company_id=? AND type='legacy_assessment'", (acme["id"],))
        self.assertEqual([r["layer"] for r in legacy], ["B"])

    def test_ukraine_city_dropped(self):
        store = SqliteStore()
        pipeline.run_source(store, legacy_notion.Legacy(self.ROWS), None, 10)
        kyiv = one(store, "Kyiv Drones")
        self.assertEqual((kyiv["country"], kyiv["city"]), ("UA", None))

    def test_rerun_matches_by_page_id(self):
        store = SqliteStore()
        pipeline.run_source(store, legacy_notion.Legacy(self.ROWS), None, 10)
        rows = [dict(r, Website="https://changed.example") for r in self.ROWS]
        res = pipeline.run_source(store, legacy_notion.Legacy(rows), None, 10)
        self.assertEqual((res["new_companies"], res["matched"]), (0, 3))
        self.assertEqual(len(companies(store)), 3)


class TestOwnership(unittest.TestCase):
    def _company(self, store, name, country):
        rec = Record("s", name, {"n": name}, [Mention(name=name, country=country,
                                                      signals=[SignalIn(type="exhibition")])])
        pipeline.run_source(store, StaticAdapter("s", [rec]), None, 1)
        return one(store, name)["id"]

    def test_ru_cn_legal_country_excluded_but_kept(self):
        store = SqliteStore()
        for name, cc in (("Ru Arms", "RU"), ("Cn Parts", "CN"), ("HK Trade", "HK")):
            self._company(store, name, cc)
        self.assertEqual(one(store, "Ru Arms")["excluded_reason"], "legal_country_RU")
        self.assertEqual(one(store, "Cn Parts")["status"], "excluded")
        hk = one(store, "HK Trade")
        self.assertEqual(hk["status"], "candidate")
        self.assertIn("CN_SAR", json.loads(hk["flags"]))
        self.assertNotIn("RU", report.summary(store)["by_country"])

    def test_partial_ownership_is_shown_not_excluded(self):
        store = SqliteStore()
        cid = self._company(store, "Euro Chips", "DE")
        ownership.record_partial_ownership(store, cid, "CN", "annual_report_2025", "Some CN Fund", "24%")
        row = store.get(cid)
        self.assertEqual(row["status"], "candidate")
        self.assertIn("OWNERSHIP_LINK_CN", json.loads(row["flags"]))
        link = store.query("SELECT * FROM company_ownership_link WHERE company_id=?", (cid,))[0]
        self.assertEqual((link["owner_country"], link["share_text"], link["link_type"]), ("CN", "24%", "minority"))

    def test_gleif_exact_lei_and_parent(self):
        store = SqliteStore()
        cid = self._company(store, "Saab AB", "SE")
        sub = self._company(store, "Saab International AB", "SE")
        out = pipeline.enrich_identities(store, FakeGleif(), 10)
        self.assertEqual(out.get("no_parent"), 1)
        self.assertEqual(out.get("parent_linked"), 1)
        self.assertEqual(dict(store.identifiers_of(cid))["LEI"], "549300ZHO4JCQQI13M69")
        rel = store.query("SELECT from_id FROM company_relation WHERE to_id=? AND relation='parent_of'", (sub,))
        self.assertEqual([r["from_id"] for r in rel], [cid])  # parent found by LEI, no duplicate Saab

    def test_gleif_no_lei_parent_is_open(self):
        store = SqliteStore()
        cid = self._company(store, "Nexperia B.V.", "NL")
        out = pipeline.enrich_identities(store, FakeGleif(), 10)
        self.assertEqual(out, {"parent_unknown": 1})
        row = store.get(cid)
        self.assertEqual((row["status"], row["ownership_check"]), ("candidate", "OPEN:GLEIF_NO_LEI"))

    def test_gleif_cn_ultimate_parent_excludes(self):
        store = SqliteStore()
        cid = self._company(store, "Euro Sub GmbH", "DE")
        gleif = FakeGleif(extra_exact={("Euro Sub GmbH", "DE"): "LEI-SUB"},
                          extra_parents={"LEI-SUB": {"id": "LEI-CN", "legalName": "Big CN Group", "country": "CN"}})
        out = pipeline.enrich_identities(store, gleif, 10)
        self.assertEqual(out, {"excluded_by_parent": 1})
        row = store.get(cid)
        self.assertEqual((row["status"], row["excluded_reason"]), ("excluded", "ultimate_parent_CN"))
        self.assertEqual(one(store, "Big CN Group")["status"], "excluded")
        self.assertEqual(store.query("SELECT link_type FROM company_ownership_link")[0]["link_type"], "ultimate_parent")

    def test_gleif_skips_ambiguous_and_marks_check(self):
        store = SqliteStore()
        cid = self._company(store, "SSAB AB", "SE")
        out = pipeline.enrich_identities(store, FakeGleif(), 10)
        self.assertEqual(out, {"no_exact_lei": 1})  # no fuzzy match to "Saab"
        self.assertEqual(store.get(cid)["ownership_check"], "GLEIF_NO_EXACT_LEI")
        self.assertEqual(pipeline.enrich_identities(store, FakeGleif(), 10), {})  # not retried every run


class TestUkraineSchemaGuard(unittest.TestCase):
    def test_check_constraint_blocks_city(self):
        store = SqliteStore()
        cid = store.create_company("X", country="UA", city="Lviv")
        self.assertIsNone(store.get(cid)["city"])
        with self.assertRaises(sqlite3.IntegrityError):
            store.update(cid, city="Lviv")


class TestFailures(unittest.TestCase):  # AC7
    class Broken:
        source = "broken"

        def fetch(self, http, cursor, limit):
            raise ConnectionError("source down")

    def test_failing_source_isolated_and_disabled_after_three(self):
        store = SqliteStore()
        store.save_source_state("broken", "c-42", 0, False)
        for i in range(3):
            res = pipeline.run_source(store, self.Broken(), None, 5)
            self.assertEqual(res["status"], "error")
        st = store.source_state("broken")
        self.assertEqual((st["cursor"], st["consecutive_failures"], bool(st["disabled"])), ("c-42", 3, True))
        self.assertEqual(pipeline.run_source(store, self.Broken(), None, 5)["status"], "skipped_disabled")
        ok = pipeline.run_source(store, ted.Awards(), ted_http(), 10)
        self.assertEqual(ok["status"], "ok")

    def test_partial_writes_rolled_back(self):
        class HalfBroken:
            source = "half"

            def fetch(self, http, cursor, limit):
                return [Record("half", "1", {"x": 1}, [Mention(name="Good Co", country="FR")]),
                        Record("half", "2", {"bad": object()}, [Mention(name="Bad Co", country="FR")])], None  # not JSON

        store = SqliteStore()
        res = pipeline.run_source(store, HalfBroken(), None, 5)
        self.assertEqual(res["status"], "error")
        self.assertEqual(companies(store), [])
        self.assertEqual(store.source_state("half")["consecutive_failures"], 1)

    def test_success_resets_failures(self):
        store = SqliteStore()
        store.save_source_state(ted.SOURCE, None, 2, False)
        pipeline.run_source(store, ted.Awards(), ted_http(), 10)
        self.assertEqual(store.source_state(ted.SOURCE)["consecutive_failures"], 0)


class TestReport(unittest.TestCase):  # AC6
    def test_completeness_by_id_and_name(self):
        store = SqliteStore()
        pipeline.run_source(store, usaspending.Recipients(psc_groups=["14"]), recipients_http(), 10)
        bench = [{"name": "Lockheed Martin", "ids": [["UEI", "XFJMYSYFJEK4"]]},
                 {"name": "Raytheon Company", "country": None},
                 {"name": "BAE Systems plc", "country": "GB"}]
        res = report.completeness(store, bench)
        self.assertEqual((res["total"], res["covered"], res["missing"]), (3, 2, ["BAE Systems plc"]))
        self.assertEqual(res["share"], 0.667)

    def test_summary_shape(self):
        store = SqliteStore()
        pipeline.run_source(store, ted.Awards(), ted_http(), 10)
        s = report.summary(store)
        self.assertEqual(s["companies"], 1)
        self.assertEqual(s["by_layer"], {"A": 1})
        self.assertEqual(s["last_runs"][0]["status"], "ok")


class TestCli(unittest.TestCase):
    def test_report_only_offline(self):
        import importlib.util
        path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "companies-collect.py"
        spec = importlib.util.spec_from_file_location("companies_collect", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = cli.main(["--db", f"{tmp}/c.sqlite3", "--report"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(buf.getvalue())["summary"]["companies"], 0)


if __name__ == "__main__":
    unittest.main()
