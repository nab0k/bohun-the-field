"""Synthetic read-only DB integration tests for C3-10 live shadow adapter."""
from datetime import datetime, timezone
import unittest

from collectors.editor.live_shadow import query_latest, run_shadow, LATEST_VERSIONS_SQL


class Col:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = tuple(Col(n) for n in ("id", "event_id", "version", "generator",
            "published_at", "language", "headline", "what_happened",
            "context", "policy_direction", "bigger_picture", "supply_chain",
            "why_it_matters", "sources", "context_sources"))
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, params):
        self.conn.executed.append((sql, params))
        if not self.conn.read_only:
            raise AssertionError("SELECT ran without READ ONLY mode")
    def fetchall(self): return self.conn.rows


class FakeConn:
    def __init__(self, rows=None, read_only_confirmed=True):
        self.rows = [] if rows is None else rows
        self.executed = []
        self.read_only = False
        self.read_only_confirmed = read_only_confirmed
    def execute(self, sql):
        self.executed.append((sql, None))
        if sql == "SET TRANSACTION READ ONLY":
            self.read_only = True
            return None
        if sql == "SHOW transaction_read_only":
            return type("Row", (), {"fetchone": lambda obj: ("on" if self.read_only_confirmed else "off",)})()
        raise AssertionError("Unexpected session SQL")
    def cursor(self): return FakeCursor(self)


def row(id=42):
    return (id, 91, 1, "writer-fixture", datetime(2026,11,7,19,tzinfo=timezone.utc),
            "en", "Contract announced", "Agency announced a new award.",
            "A verified past event is relevant.", "", "", "",
            "May support industrial capacity.",
            [{"url":"https://example.org/contract", "date":"2026-11-06", "class":"P2"}],
            [])


class LiveShadowTests(unittest.TestCase):
    def test_looks_up_current_latest_with_read_only_before_select(self):
        db = FakeConn([row()])
        out = query_latest(db, lookback_hours=24, limit=30)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], 42)
        self.assertEqual(out[0]["published_at"], "2026-11-07T19:00:00+00:00")
        self.assertEqual(db.executed[:2], [("SET TRANSACTION READ ONLY", None),
                                            ("SHOW transaction_read_only", None)])
        self.assertIn("DISTINCT ON (s.id)", db.executed[2][0])
        self.assertIn("v.version DESC", db.executed[2][0])
        self.assertEqual(db.executed[2][1], (24, 30))

    def test_denies_query_when_read_only_not_confirmed(self):
        db = FakeConn([row()], read_only_confirmed=False)
        with self.assertRaisesRegex(RuntimeError, "database_read_only_unconfirmed"):
            query_latest(db)
        self.assertEqual(len(db.executed), 2)

    def test_invalid_limit_zero(self):
        db = FakeConn([row()])
        with self.assertRaisesRegex(ValueError, "invalid_limit"):
            query_latest(db, limit=0)
        self.assertFalse(db.executed)

    def test_limit_over_cap(self):
        with self.assertRaisesRegex(ValueError, "invalid_limit"):
            query_latest(FakeConn(), limit=31)

    def test_invalid_lookback(self):
        with self.assertRaisesRegex(ValueError, "invalid_lookback_hours"):
            query_latest(FakeConn(), lookback_hours=169)

    def test_boolean_limit_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_limit"):
            query_latest(FakeConn(), limit=True)

    def test_empty_window_safe_report(self):
        result=run_shadow(FakeConn())
        self.assertEqual(result["count"],0)
        self.assertEqual(result["decisions"], {"PASS":0,"REVIEW":0,"BLOCK":0})
        self.assertFalse(result["publication_authorized"])

    def test_actual_structural_shadow_never_passes(self):
        result=run_shadow(FakeConn([row()]))
        self.assertEqual(result["count"],1)
        self.assertEqual(result["decisions"]["PASS"],0)
        self.assertEqual(result["decisions"]["REVIEW"],1)
        self.assertFalse(result["independent_fact_check_performed"])
        self.assertEqual(result["database_writes"],0)
        self.assertEqual(result["model_calls"],0)

    def test_no_source_text_leak(self):
        r=run_shadow(FakeConn([row()]))
        self.assertNotIn("Agency announced a new award.",str(r))
        self.assertNotIn("A verified past event",str(r))

    def test_db_oversized_return_rejected(self):
        with self.assertRaisesRegex(ValueError,"database_row_limit_exceeded"):
            query_latest(FakeConn([row(i) for i in range(2)]), limit=1)

    def test_no_admission_approval_filter(self):
        self.assertNotIn("synthesis_admission",LATEST_VERSIONS_SQL)
        self.assertNotIn("PUBLISH",LATEST_VERSIONS_SQL)
        self.assertNotIn("UPDATE",LATEST_VERSIONS_SQL.upper())
        self.assertNotIn("INSERT",LATEST_VERSIONS_SQL.upper())
        self.assertNotIn("DELETE",LATEST_VERSIONS_SQL.upper())

    def test_bad_json_source_blocks_fail_closed(self):
        o=list(row())
        o[-2]=[{"url":"http://localhost/invalid", "date":"2026-11-06"}]
        result=run_shadow(FakeConn([tuple(o)]))
        self.assertEqual(result["decisions"]["BLOCK"],1)
        self.assertFalse(result["publication_authorized"])


if __name__ == "__main__":
    unittest.main()
