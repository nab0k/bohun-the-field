"""DISCOVERY-SCALE01 offline regression tests. No network, no production DB."""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from collectors.companies import pipeline, report
from collectors.companies.model import Record, Discovery, Mention
from collectors.companies.sources import eurosatory
from collectors.companies.sources.http import SourceError
from collectors.companies.store import SqliteStore

HTML = (pathlib.Path(__file__).parent / "fixtures" / "eurosatory_sample.html").read_bytes()
DIGEST = hashlib.sha256(HTML).hexdigest()


class FakeHttp:
    def __init__(self, data=HTML):
        self.data = data
    def get_bytes(self, url, accept="*/*"):
        assert url == eurosatory.URL and accept == "text/html"
        return self.data


class StaticAdapter:
    source = "discovery_test"
    def __init__(self, records):
        self.records = records
    def fetch(self, http, cursor, limit):
        return self.records[:limit], "done:test"


class Tests(unittest.TestCase):
    def test_nested_headers_and_dedup(self):
        self.assertEqual(eurosatory.parse_html(HTML),
            [("Alpha Systems","France"),("Beta Defence","Germany"),("Nova","Ukraine")])
    def test_structural_nested_cells(self):
        html = (b"<table><tr><th>EXHIBITOR</th><th>COUNTRY</th></tr>"
                b"<tr><td>North<br>Star <span>Systems</span></td><td><div>United</div>"
                b"<div>Kingdom</div></td></tr></table>")
        self.assertEqual(eurosatory.parse_html(html),
                         [("North Star Systems", "United Kingdom")])
    def test_discovery_only_adapter_rejects_mention_and_rolls_back(self):
        store = SqliteStore()
        adapter = eurosatory.Exhibitors2024()
        valid, _ = adapter.fetch(FakeHttp(), None, 1)
        corrupt = Record(eurosatory.SOURCE, valid[0].source_key, valid[0].payload,
                         mentions=[Mention("Must Not Be Created", country="FR")],
                         discoveries=valid[0].discoveries)
        adapter.fetch = lambda http, cursor, limit: ([corrupt], "done:fixture")
        result = pipeline.run_source(store, adapter, None, 1)
        self.assertEqual(result["status"], "error")
        self.assertEqual(store.query("SELECT COUNT(*) n FROM company")[0]["n"], 0)
        self.assertEqual(store.query("SELECT COUNT(*) n FROM observation")[0]["n"], 0)
    def test_fail_closed(self):
        for raw in (b"<table><tr><td>Foo</td></tr></table>",
                    b"<table><tr><th>EXHIBITOR</th><th>COUNTRY</th></tr></table>"):
            with self.assertRaises(SourceError):
                eurosatory.parse_html(raw)
    def test_cursor_and_hash_change(self):
        e = eurosatory.Exhibitors2024()
        a,c = e.fetch(FakeHttp(),None,2)
        self.assertEqual(len(a),2)
        self.assertEqual(c,f"o:2:{DIGEST}")
        b,done = e.fetch(FakeHttp(),c,2)
        self.assertEqual((len(b),done),(1,f"done:{DIGEST}"))
        self.assertEqual(e.fetch(FakeHttp(),done,3)[0],[])
        self.assertEqual(len(e.fetch(FakeHttp(HTML+b"\n"),done,3)[0]),3)
    def test_snapshot_immutable(self):
        with tempfile.TemporaryDirectory() as d:
            e=eurosatory.Exhibitors2024(d)
            e.fetch(FakeHttp(),None,3)
            path=pathlib.Path(d)/f"eurosatory_2024_exhibitors-{DIGEST}.html"
            self.assertEqual(path.read_bytes(),HTML)
            e.fetch(FakeHttp(),None,3)
            self.assertEqual(path.read_bytes(),HTML)
    def test_source_key_and_payload(self):
        r=eurosatory.Exhibitors2024().fetch(FakeHttp(),None,1)[0][0]
        self.assertEqual(r.source_key,hashlib.sha256(b"Alpha Systems\x1fFrance").hexdigest())
        self.assertEqual(r.payload["snapshot_sha256"],DIGEST)
        self.assertEqual(r.payload["snapshot_bytes"],len(HTML))
        self.assertEqual((len(r.mentions),len(r.discoveries)),(0,1))
    def test_discovery_zero_companies(self):
        store=SqliteStore()
        records,_=eurosatory.Exhibitors2024().fetch(FakeHttp(),None,10)
        result=pipeline.run_source(store,StaticAdapter(records),None,10)
        self.assertEqual((result["discoveries"],result["new_companies"]),(3,0))
        self.assertEqual(report.summary(store)["discovery_candidates"],3)
        self.assertEqual(store.query("SELECT COUNT(*) n FROM company")[0]["n"],0)
    def test_exact_match_does_not_change_legal_country(self):
        store=SqliteStore()
        cid=store.create_company("Alpha Systems",country="SE")
        rec=Record("discovery_test","1",{"s":1},discoveries=[Discovery("Alpha Systems","France")])
        result=pipeline.run_source(store,StaticAdapter([rec]),None,1)
        d=store.query("SELECT * FROM discovery_candidate")[0]
        self.assertEqual((result["candidate_existing"],result["new_companies"]),(1,0))
        self.assertEqual((d["status"],d["matched_company_id"],d["match_reason"]),("candidate_existing",cid,"exact_normalized_name_only"))
        self.assertEqual(store.get(cid)["country"],"SE")
        self.assertEqual(d["reported_country"],"France")
    def test_multiple_matches_hold(self):
        store=SqliteStore()
        a=store.create_company("Nova",country="US")
        b=store.create_company("Nova",country="GB")
        rec=Record("discovery_test","1",{"s":1},discoveries=[Discovery("Nova","France")])
        result=pipeline.run_source(store,StaticAdapter([rec]),None,1)
        self.assertEqual(result["holds"],1)
        self.assertEqual(store.query("SELECT status,match_reason FROM discovery_candidate")[0]["match_reason"],"multiple_exact_normalized_name_matches")
        self.assertEqual({x["company_id"] for x in store.query("SELECT * FROM match_candidate")},{a,b})
    def test_replay_no_new_observations(self):
        store=SqliteStore()
        recs,_=eurosatory.Exhibitors2024().fetch(FakeHttp(),None,5)
        a=StaticAdapter(recs)
        pipeline.run_source(store,a,None,5)
        store.save_source_state(a.source,None,0,False)
        store.commit()
        second=pipeline.run_source(store,a,None,5)
        self.assertEqual(second["new_observations"],0)
        self.assertEqual(store.query("SELECT COUNT(*) n FROM discovery_candidate")[0]["n"],3)
    def test_counters_include_matched_and_held_discoveries(self):
        store = SqliteStore()
        store.create_company("Alpha Systems", country="SE")
        store.create_company("Nova", country="FR")
        store.create_company("Nova", country="US")
        rows, _ = eurosatory.Exhibitors2024().fetch(FakeHttp(), None, 10)
        out = pipeline.run_source(store, eurosatory.Exhibitors2024(), FakeHttp(), 10)
        self.assertEqual(out["new_companies"], 0)
        self.assertEqual(out["new_observations"], 3)
        self.assertEqual(out["discoveries"], 3)  # all accepted candidates
        self.assertEqual(out["candidate_existing"], 1)
        self.assertEqual(out["holds"], 1)
        self.assertEqual(report.summary(store)["discovery_candidates"], 3)

    def test_cosmetic_live_html_change_does_not_duplicate_observations(self):
        # Official WordPress emits different raw HTML hashes while exhibitors stay identical.
        store = SqliteStore()
        source = eurosatory.Exhibitors2024()
        one = pipeline.run_source(store, source, FakeHttp(HTML), 10)
        cosmetic = HTML + b"\n<!-- harmless footer change -->"
        two = pipeline.run_source(store, source, FakeHttp(cosmetic), 10)
        self.assertEqual(one["new_observations"], 3)
        self.assertEqual(two["fetched"], 3)  # new snapshot triggers complete scan
        self.assertEqual(two["new_observations"], 0)
        self.assertEqual(two["discoveries"], 0)
        self.assertEqual(two["cursor_out"], "done:" + hashlib.sha256(cosmetic).hexdigest())
        self.assertEqual(report.summary(store)["discovery_candidates"], 3)
        self.assertEqual(store.query("SELECT COUNT(*) n FROM company")[0]["n"], 0)

    def test_added_exhibitor_discovered_when_raw_html_changes(self):
        store = SqliteStore()
        source = eurosatory.Exhibitors2024()
        pipeline.run_source(store, source, FakeHttp(HTML), 10)
        changed = HTML.replace(b"</table></body>", b"<tr><td>New Entrant</td><td>Portugal</td></tr></table></body>")
        two = pipeline.run_source(store, source, FakeHttp(changed), 10)
        self.assertEqual(two["fetched"], 4)
        self.assertEqual(two["new_observations"], 1)
        self.assertEqual(two["discoveries"], 1)
        self.assertEqual(two["new_companies"], 0)
        self.assertEqual(report.summary(store)["discovery_candidates"], 4)

    def test_cli_verified_offline_snapshot(self):
        script = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "companies-collect.py"
        fixture = pathlib.Path(__file__).parent / "fixtures" / "eurosatory_sample.html"
        cmd = [sys.executable, str(script), "--dry-run", "--sources", "eurosatory2024",
               "--snapshot-file", str(fixture), "--expected-sha256", DIGEST,
               "--limit", "2500", "--report"]
        good = subprocess.run(cmd, text=True, capture_output=True, check=False)
        self.assertEqual(good.returncode, 0, good.stderr)
        result = json.loads(good.stdout)
        self.assertEqual(result["eurosatory2024"]["discoveries"], 3)
        self.assertEqual(result["eurosatory2024"]["new_companies"], 0)
        self.assertEqual(result["summary"]["discovery_candidates"], 3)
        bad = subprocess.run(cmd[:-5] + ["--expected-sha256", "0"*64,
                             "--limit", "2500", "--report"],
                             text=True, capture_output=True, check=False)
        self.assertNotEqual(bad.returncode, 0)

    def test_bulk_2015_synthetic_rows_and_unchanged_done(self):
        # Scale smoke only: NOT the real historical Eurosatory HTML or its SHA-256.
        names = "".join(f"<tr><td>Fixture Exhibitor {i:04d}</td><td>Fixtureland</td></tr>"
                        for i in range(2015))
        html = (("<table><tr><th>EXHIBITOR</th><th>COUNTRY</th></tr>"
                 + names + "</table>").encode("utf-8"))
        digest = hashlib.sha256(html).hexdigest()
        store = SqliteStore()
        source = eurosatory.Exhibitors2024()
        first = pipeline.run_source(store, source, FakeHttp(html), 2500)
        self.assertEqual(first["status"], "ok")
        self.assertEqual(first["fetched"], 2015)
        self.assertEqual(first["new_observations"], 2015)
        self.assertEqual(first["discoveries"], 2015)
        self.assertEqual(first["new_companies"], 0)
        self.assertEqual(first["cursor_out"], "done:" + digest)
        self.assertEqual(report.summary(store)["discovery_candidates"], 2015)
        self.assertEqual(store.query("SELECT COUNT(*) n FROM company")[0]["n"], 0)
        replay = pipeline.run_source(store, source, FakeHttp(html), 2500)
        self.assertEqual((replay["fetched"], replay["new_observations"]), (0, 0))
        self.assertEqual(report.summary(store)["discovery_candidates"], 2015)

    def test_alias_only_candidate(self):
        store=SqliteStore()
        cid=store.create_company("Primary",country="FI")
        store.add_alias(cid,"Alt Systems","test")
        rec=Record("discovery_test","1",{"x":1},discoveries=[Discovery("Alt Systems","Germany")])
        pipeline.run_source(store,StaticAdapter([rec]),None,1)
        self.assertEqual(store.query("SELECT matched_company_id FROM discovery_candidate")[0]["matched_company_id"],cid)

if __name__ == "__main__":
    unittest.main()
