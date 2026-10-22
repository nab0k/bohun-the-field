"""R7 x real R6 code: reader -> pilot -> staging writer, against a stateful fake DB.

The fake DB keeps rows between runs, enforces the schema's unique keys and
restores its state on rollback. It is NOT PostgreSQL: grants, RLS and CHECKs
are verified only by the read-only preflight and a dry run on the real database.
"""
import copy
import json
import os
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uk_staging_writer as w

WINDOW = ("2026-10-15T09:00:00", "2026-10-15T10:00:00")
ENV = {"BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF": "owner-approved:synthetic-test",
       "BOHUN_UK_STAGING_DSN": "synthetic", "BOHUN_UK_DB_WRITER_LOGIN": "bohun_uk_ingest_login"}
SAFE_ROLE = (False, False, False, False, False, False, False, True, True)


def release(n, tag="tender", title=None, minute=5):
    return {"ocid": f"ocds-h6vhtk-{n:06d}", "id": f"{n:06d}-2026",
            "date": f"2026-10-15T09:{minute:02d}:00+01:00", "tag": [tag],
            "tender": {"title": title or f"Synthetic UK tender {n}"}}


def page(*rows):
    return json.dumps({"releases": list(rows), "links": {}}).encode()


class FakeDB:
    def __init__(self, role=SAFE_ROLE, fail_on=None):
        self.notices, self.versions, self.receipts = {}, [], []
        self.role, self.fail_on, self.sql = role, fail_on, []

    def state(self):
        return copy.deepcopy((self.notices, self.versions, self.receipts))

    def connect(self, *args, **kwargs):
        return FakeConn(self)


class FakeConn:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        saved = self.db.state()
        try:
            yield self
        except BaseException:
            self.db.notices, self.db.versions, self.db.receipts = saved
            raise

    def cursor(self):
        return FakeCur(self.db)


class FakeCur:
    def __init__(self, db):
        self.db, self.last = db, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def fetchone(self):
        return self.last

    def execute(self, sql, args=None):
        db = self.db
        db.sql.append(sql)
        if db.fail_on and db.fail_on in sql:
            raise RuntimeError("forced_failure")
        self.last = None
        if sql.startswith("SELECT current_user"):
            self.last = ("bohun_uk_ingest_login",)
        elif sql.startswith("SELECT r.rolsuper"):
            self.last = db.role
        elif sql.startswith("SELECT id,ocid"):
            n = db.notices.get(args[0])
            self.last = n and (n["id"], n["ocid"], n["release_id"], n["sha"])
        elif sql.startswith("INSERT INTO bohun_uk_intake.notices"):
            ext, ocid, rid, title, at, sha, tags, meta = args
            if any((n["ocid"], n["release_id"]) == (ocid, rid) for n in db.notices.values()):
                raise RuntimeError("unique_violation uk_release_identity")
            nid = len(db.notices) + 1
            db.notices[ext] = dict(id=nid, ocid=ocid, release_id=rid, title=title, sha=sha,
                                   meta=meta, seen=1, qa="HOLD", public_release=False)
            self.last = (nid,)
        elif sql.startswith("UPDATE bohun_uk_intake.notices SET last_seen_at"):
            next(n for n in db.notices.values() if n["id"] == args[0])["seen"] += 1
        elif sql.startswith("UPDATE bohun_uk_intake.notices SET title"):
            n = next(n for n in db.notices.values() if n["id"] == args[5])
            n.update(title=args[0], sha=args[2], meta=args[4], seen=n["seen"] + 1)
        elif sql.startswith("INSERT INTO bohun_uk_intake.notice_versions"):
            if any(v[0] == args[0] and v[2] == args[2] for v in db.versions):
                raise RuntimeError("unique_violation uk_version_unique")
            db.versions.append(args)
        elif sql.startswith("INSERT INTO bohun_uk_intake.run_receipts"):
            db.receipts.append(args)
            self.last = (f"receipt-{len(db.receipts)}",)
        elif sql.startswith(("SELECT pg_advisory_xact_lock", "SET LOCAL", "SET TRANSACTION")):
            pass
        else:
            raise AssertionError("unexpected SQL: " + sql[:60])


def run(db, rows, *, dry_run):
    with patch.dict(os.environ, ENV):
        return w.write_live_window(*WINDOW, dry_run=dry_run, connect=db.connect,
                                   transport=lambda url, **kw: page(*rows))


class SourceWindowTests(unittest.TestCase):
    def test_bst_hour_is_utc_minus_one(self):
        a, b = w.source_window_utc(*WINDOW)
        self.assertEqual((a, b), (datetime(2026, 10, 15, 8, tzinfo=timezone.utc),
                                  datetime(2026, 10, 15, 9, tzinfo=timezone.utc)))

    def test_gmt_hour_equals_utc(self):
        a, _ = w.source_window_utc("2026-12-01T09:00:00", "2026-12-01T10:00:00")
        self.assertEqual(a, datetime(2026, 12, 1, 9, tzinfo=timezone.utc))

    def test_autumn_ambiguous_hour_refused(self):
        with self.assertRaises(w.StagingError):
            w.source_window_utc("2026-10-25T01:00:00", "2026-10-25T01:30:00")

    def test_spring_missing_hour_refused(self):
        with self.assertRaises(w.StagingError):
            w.source_window_utc("2026-03-29T00:30:00", "2026-03-29T01:30:00")

    def test_utc_receipt_bounds_rejected_for_uk_local_data(self):
        import uk_hold_pilot
        m = uk_hold_pilot.prepare_releases([json.loads(page(release(1)))], verified_complete=True)
        with self.assertRaisesRegex(w.StagingError, "outside_receipt_window"):
            w.validate_manifest(m, window_start=datetime(2026, 10, 15, 9, tzinfo=timezone.utc),
                                window_end=datetime(2026, 10, 15, 10, tzinfo=timezone.utc))


class PipelineTests(unittest.TestCase):
    rows = [release(1), release(2), release(3), release(4, tag="award")]

    def test_dry_run_executes_then_leaves_nothing(self):
        db = FakeDB()
        x = run(db, self.rows, dry_run=True)
        self.assertEqual((x["dry_run"], x["committed"]), (True, False))
        self.assertEqual(x["counts"], {"inserted": 3, "updated": 0, "repeated": 0})
        self.assertEqual(db.state(), ({}, [], []))

    def test_commit_then_rerun_has_no_duplicates(self):
        db = FakeDB()
        first = run(db, self.rows, dry_run=False)
        second = run(db, self.rows, dry_run=False)
        self.assertEqual(first["counts"], {"inserted": 3, "updated": 0, "repeated": 0})
        self.assertEqual(second["counts"], {"inserted": 0, "updated": 0, "repeated": 3})
        self.assertEqual(len(db.notices), 3)
        self.assertEqual([r[2:] for r in db.receipts], [(4, 3, 3, 0, 0), (4, 3, 0, 0, 3)])
        self.assertTrue(all(n["qa"] == "HOLD" and n["public_release"] is False
                            for n in db.notices.values()))
        self.assertFalse(any("intelligence." in s and "has_table_privilege" not in s
                             for s in db.sql))
        self.assertFalse(any("checkpoints" in s and "has_table_privilege" not in s
                             for s in db.sql))

    def test_changed_source_record_creates_version(self):
        db = FakeDB()
        run(db, self.rows, dry_run=False)
        x = run(db, [release(1, title="Synthetic UK tender 1 (amended)"), release(2), release(3)],
                dry_run=False)
        self.assertEqual(x["counts"], {"inserted": 0, "updated": 1, "repeated": 2})
        self.assertEqual(len(db.versions), 1)
        self.assertEqual(db.notices["ocds-h6vhtk-000001::000001-2026"]["title"],
                         "Synthetic UK tender 1 (amended)")

    def test_failure_rolls_back_everything(self):
        db = FakeDB(fail_on="INSERT INTO bohun_uk_intake.run_receipts")
        with self.assertRaises(RuntimeError):
            run(db, self.rows, dry_run=False)
        self.assertEqual(db.state(), ({}, [], []))

    def test_broad_privileges_stop_before_writes(self):
        for i in range(7):
            role = list(SAFE_ROLE)
            role[i] = True
            db = FakeDB(role=tuple(role))
            with self.assertRaisesRegex(w.StagingError, "privileges_not_minimal"):
                run(db, self.rows, dry_run=False)
            self.assertFalse(any(s.startswith(("INSERT", "UPDATE")) for s in db.sql))

    def test_missing_writer_membership_stops(self):
        db = FakeDB(role=SAFE_ROLE[:8] + (False,))
        with self.assertRaises(w.StagingError):
            run(db, self.rows, dry_run=False)

    def test_more_than_ten_tenders_refused_before_db(self):
        db = FakeDB()
        with self.assertRaisesRegex(w.StagingError, "1_to_10|more_than_10"):
            run(db, [release(n) for n in range(1, 12)], dry_run=True)
        self.assertEqual(db.sql, [])

    def test_dry_run_flag_must_be_explicit_bool(self):
        with self.assertRaises(w.StagingError):
            run(FakeDB(), self.rows, dry_run="no")


class ConnectionCheckTests(unittest.TestCase):
    def test_check_only_writes_nothing(self):
        db = FakeDB()
        with patch.dict(os.environ, ENV):
            x = w.check_connection(connect=db.connect)
        self.assertEqual(x, {"login": "bohun_uk_ingest_login", "privileges": "minimal", "writes": 0})
        self.assertFalse(any(s.startswith(("INSERT", "UPDATE")) for s in db.sql))

    def test_check_only_stops_on_broad_role(self):
        db = FakeDB(role=(True,) + SAFE_ROLE[1:])
        with patch.dict(os.environ, ENV), self.assertRaises(w.StagingError):
            w.check_connection(connect=db.connect)

    def test_password_in_dsn_refused(self):
        for dsn in ("host=h user=u password=x", "postgresql://u:x@h/postgres"):
            with patch.dict(os.environ, {**ENV, "BOHUN_UK_STAGING_DSN": dsn}):
                with self.assertRaisesRegex(w.StagingError, "must_not_contain_password"):
                    w.check_connection(connect=FakeDB().connect)


@unittest.skipUnless(os.environ.get("BOHUN_UK_REAL_PAGE"), "set BOHUN_UK_REAL_PAGE to a saved read-only page")
class RealPageTests(unittest.TestCase):
    def test_real_page_dry_run(self):
        raw = Path(os.environ["BOHUN_UK_REAL_PAGE"]).read_bytes()
        db = FakeDB()
        with patch.dict(os.environ, ENV):
            x = w.write_live_window(*WINDOW, dry_run=True, connect=db.connect,
                                    transport=lambda url, **kw: raw)
        self.assertEqual(x["candidate_count"], 10)
        self.assertEqual(x["counts"]["inserted"], 10)
        self.assertEqual(db.state(), ({}, [], []))


if __name__ == "__main__":
    unittest.main()
