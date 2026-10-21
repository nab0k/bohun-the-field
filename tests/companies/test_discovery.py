"""DISCOVERY-SCALE01 offline regression tests. No network, no production DB."""
import hashlib
import pathlib
import tempfile
import unittest

from collectors.companies import pipeline, report
from collectors.companies.model import Record, Discovery
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
    def test_alias_only_candidate(self):
        store=SqliteStore()
        cid=store.create_company("Primary",country="FI")
        store.add_alias(cid,"Alt Systems","test")
        rec=Record("discovery_test","1",{"x":1},discoveries=[Discovery("Alt Systems","Germany")])
        pipeline.run_source(store,StaticAdapter([rec]),None,1)
        self.assertEqual(store.query("SELECT matched_company_id FROM discovery_candidate")[0]["matched_company_id"],cid)

if __name__ == "__main__":
    unittest.main()
