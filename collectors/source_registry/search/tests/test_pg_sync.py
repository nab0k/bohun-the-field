"""pg_sync with a fake connection (no database): the shapes load() expects and what save() writes."""
import contextlib
import datetime as dt
import sys
import tempfile
import types
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import pg_sync  # noqa: E402
import admission as A  # noqa: E402

T = dt.datetime(2026, 11, 19, 8, 0, tzinfo=dt.timezone.utc)


class Cursor:
    def __init__(self, pg):
        self.pg, self.rows = pg, []

    def execute(self, sql, params=None):
        self.pg.sql.append((sql.strip().split()[0].lower(), " ".join(sql.split())[:80], params))
        s = " ".join(sql.split())
        if "from source_discovery.source_state" in s:
            self.rows = [("ch1", "https://old.example.org/feed", "accepted", "media", "feed", "PL", "pl", T, "a2sa-shadow-0.5")]
        elif "from source_discovery.candidate_seen" in s:
            self.rows = [("https://old.example.org/", "accept", T, 0, "ch1")]
        elif "from source_discovery.admission_decision" in s:
            self.rows = [("https://old.example.org/", "ch1", "accept", ["x"], {"decision": "accept"}, "a2sa-shadow-0.5", T,
                          {"layer": "layer1"})]
        elif "seed_known_hosts" in s:
            self.rows = [(["seen-on-mac.example"],)]
        elif "jsonb_array_elements_text" in s:
            self.rows = [(["layer1|PL|ew||pl|"],)]
        elif "from companies.company" in s:
            self.rows = [(True, "tno.nl", "NL"), (False, "saab.com", "SE "), (False, None, None)]
        elif "intelligence.sources" in s:
            self.rows = [("defence24.com",)]
        elif "knowledge.event" in s:
            self.rows = [(1, "Event", "electronic-warfare", ["a.com"])]
        else:
            self.rows = []

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class FakePG:
    def __init__(self):
        self.sql = []

    def cursor(self):
        return Cursor(self)

    @contextlib.contextmanager
    def transaction(self):
        yield


class Sync(unittest.TestCase):
    def test_load_then_save_only_new(self):
        fake_json = types.ModuleType("psycopg.types.json")
        fake_json.Jsonb = lambda v: v
        sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))
        sys.modules.setdefault("psycopg.types", types.ModuleType("psycopg.types"))
        sys.modules["psycopg.types.json"] = fake_json
        pg = FakePG()
        with tempfile.TemporaryDirectory() as tmp:
            conn, ctx = pg_sync.load(pg, Path(tmp) / "r.sqlite")
            self.assertEqual((ctx["company"], ctx["rnd"], ctx["known"]), ({"saab.com"}, {"tno.nl"}, {"defence24.com", "seen-on-mac.example"}))
            self.assertEqual(ctx["done"], ["layer1|PL|ew||pl|"])
            self.assertEqual(ctx["company_country"]["saab.com"], "SE")
            self.assertEqual(ctx["events"][0]["known_hosts"], ["a.com"])
            self.assertEqual(ctx["loaded_max_decision"], 1)
            start = dt.datetime.now(dt.timezone.utc)
            new_obs = {"candidate": {"url": "https://new.example.org/", "found_via": {"layer": "types"}},
                       "fetch": {"status": 404}, "robots": {}}
            A.admit(conn, [new_obs])
            pg.sql.clear()
            out = pg_sync.save(pg, conn, ctx, {"run_id": "r1", "started_at": start.isoformat(),
                                               "finished_at": start.isoformat(), "where_run": "test", "plan": {},
                                               "trace": {}, "measure": {}, "done_jobs": ["types|PL||procurement|en|"]})
            self.assertEqual(out, {"decisions": 1, "states": 0, "seen": 1})
            kinds = [s[1].split(" (")[0] for s in pg.sql]
            self.assertTrue(any("source_discovery.admission_decision" in k for k in kinds))
            self.assertTrue(any("source_discovery.cycle_run" in k for k in kinds))
            self.assertFalse(any("intelligence" in s[1] or "companies" in s[1] for s in pg.sql))  # never writes product
            conn.close()


class Daily(unittest.TestCase):
    def test_company_layer_and_recheck(self):
        import run_daily as R
        ctx = {"company": {"saab.com", "known.example"}, "rnd": {"tno.nl"}, "company_country": {"saab.com": "SE"}}
        cands = R.company_candidates(ctx, {"known.example"}, n=5)
        self.assertEqual([c["url"] for c in cands], ["https://saab.com/", "https://tno.nl/"])
        self.assertEqual(cands[0]["found_via"]["country"], "SE")
        with tempfile.TemporaryDirectory() as tmp:
            conn = A.open_db(str(Path(tmp) / "r.sqlite"))
            old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20)).isoformat()
            for url, reason in (("https://stale.example/", "stale"), ("https://plain.example/", "no_news_site")):
                conn.execute("INSERT INTO candidate_seen VALUES (?,?,?,?,?)", (url, "hold", old, 0, None))
                conn.execute("INSERT INTO admission_decision(candidate_url, decision, reasons_json, record_json, "
                             "rules_version, decided_at) VALUES (?,?,?,?,?,?)",
                             (url, "hold", '["%s"]' % reason, "{}", "a2sa-shadow-0.5", old))
            again = R.recheck_candidates(conn, n=5)
            self.assertEqual([c["url"] for c in again], ["https://stale.example/"])  # no-news sites wait for their own pass
            obs = {"candidate": again[0], "fetch": {"status": 404}, "robots": {}}
            counts, _ = A.admit(conn, [obs])
            self.assertEqual(counts["skipped_decided"], 1)            # without recheck: skipped
            counts, _ = A.admit(conn, [obs], recheck={"https://stale.example/"})
            self.assertEqual(counts["reject"], 1)                     # with recheck: decided again
            conn.close()


if __name__ == "__main__":
    unittest.main()
