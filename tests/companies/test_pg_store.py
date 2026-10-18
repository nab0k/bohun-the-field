"""Offline tests for the PostgreSQL store: SQL translation and the same pipeline over a fake connection."""
import json
import unittest

from collectors.companies import pipeline, report
from collectors.companies.pg_store import PgStore, translate
from collectors.companies.store import SqliteStore
from tests.companies.helpers import FakeHttp, fixture
from collectors.companies.sources import ted


class TestTranslate(unittest.TestCase):
    def test_or_ignore_and_placeholders(self):
        self.assertEqual(translate("INSERT OR IGNORE INTO company_alias VALUES (?,?,?,?)"),
                         "INSERT INTO company_alias VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING")

    def test_null_safe_equality(self):
        self.assertIn("c.country IS NOT DISTINCT FROM %s", translate("SELECT 1 WHERE c.country IS ?"))
        self.assertIn("ownership_check IS NULL", translate("SELECT 1 WHERE ownership_check IS NULL"))

    def test_upsert_unchanged_apart_from_placeholders(self):
        sql = "INSERT INTO source_state VALUES (?,?) ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor"
        self.assertEqual(translate(sql), sql.replace("?", "%s"))

    def test_percent_rejected(self):
        with self.assertRaises(ValueError):
            translate("SELECT 1 WHERE name LIKE '%x'")


class SqliteAsPg:
    """Fake psycopg connection: runs translated SQL back on SQLite to prove every statement goes through _q."""

    def __init__(self):
        self.inner = SqliteStore()
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if sql.startswith("SET search_path"):
            return None
        back = sql.replace("%s", "?").replace("IS NOT DISTINCT FROM", "IS")
        if back.endswith(" ON CONFLICT DO NOTHING"):
            back = "INSERT OR IGNORE INTO" + back[len("INSERT INTO"):-len(" ON CONFLICT DO NOTHING")]
        return self.inner.db.execute(back, params)

    def commit(self):
        self.inner.db.commit()

    def rollback(self):
        self.inner.db.rollback()


class TestPgStoreOverFake(unittest.TestCase):
    def test_pipeline_runs_through_translation(self):
        conn = SqliteAsPg()
        store = PgStore(conn)
        http = FakeHttp(post={ted.API: dict(fixture("ted_awards.json"), iterationNextToken=None)})
        res = pipeline.run_source(store, ted.Awards(), http, 10)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(report.summary(store)["companies"], 1)
        self.assertTrue(all("?" not in s for s in conn.statements))
        sample = report.sample(store, 20)
        self.assertEqual(sample["listed"][0]["name"], "Weibel Scientific A/S")
        self.assertEqual(sample["ukraine_not_listed"], 0)

    def test_flags_accept_jsonb_list(self):
        conn = SqliteAsPg()
        store = PgStore(conn)
        cid = store.create_company("X", country="FR")
        original_get = store.get
        store.get = lambda c: dict(original_get(c), flags=["A"])  # psycopg returns jsonb as a list
        store.add_flags(cid, ["B"])
        store.get = original_get
        self.assertEqual(json.loads(store.get(cid)["flags"]), ["A", "B"])


class TestSampleHidesUkraine(unittest.TestCase):
    def test_ua_counted_not_listed(self):
        store = SqliteStore()
        store.create_company("Kyiv Co", country="UA")
        store.create_company("Paris Co", country="FR")
        s = report.sample(store, 5)
        self.assertEqual([r["name"] for r in s["listed"]], ["Paris Co"])
        self.assertEqual(s["ukraine_not_listed"], 1)


if __name__ == "__main__":
    unittest.main()
