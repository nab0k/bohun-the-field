"""Stage 1 (AC1/AC2): fields, sanitizing, versions by ocid, against a stateful fake DB.

The fake DB keeps rows between runs, enforces the M1 unique keys and the
no-contactPoint rule, and restores its state on rollback. It is NOT PostgreSQL:
grants, RLS and CHECKs are proven only by the read-only preflight and a dry run.
"""
import copy
import json
import os
import re
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uk_fields
import uk_stage_writer_v2 as w2
import uk_window

ENV = {"BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF": "owner-approved:synthetic-test",
       "BOHUN_UK_STAGING_DSN": "synthetic", "BOHUN_UK_DB_WRITER_LOGIN": "bohun_uk_ingest_login"}
SAFE_ROLE = (False, False, False, False, False, False, False, True, True)
LICENSE = "http://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
T0 = datetime(2026, 10, 15, 8, tzinfo=timezone.utc)


def rel(ocid="ocds-x-1", rid="000001-2026", tag="tender", minute=5, title="Synthetic tender",
        amount=100.0, deadline="2026-11-12T12:00:00Z", partial=False, extra=None):
    tender = {"id": rid, "title": title, "description": "Synthetic description " + rid}
    if not partial:
        tender.update({"status": "active", "procurementMethod": "open",
                       "mainProcurementCategory": "goods",
                       "value": {"amount": amount, "amountGross": amount * 1.2, "currency": "GBP"},
                       "tenderPeriod": {"endDate": deadline}, "lots": [{"id": "1"}],
                       "aboveThreshold": True,
                       "items": [{"id": "1", "additionalClassifications": [
                           {"scheme": "CPV", "id": "35613000", "description": "UAV"}]}],
                       "documents": [{"documentType": "tenderNotice",
                                      "url": "https://www.find-tender.service.gov.uk/Notice/" + rid}]})
    tender.update(extra or {})
    return {"ocid": ocid, "id": rid, "tag": [tag], "date": f"2026-10-15T09:{minute:02d}:00+01:00",
            "buyer": {"id": "GB-BUYER-1", "name": "Synthetic Buyer"},
            "parties": [{"id": "GB-BUYER-1", "roles": ["buyer"], "name": "Synthetic Buyer",
                         "address": {"countryName": "United Kingdom"},
                         "contactPoint": {"name": "Synthetic Person", "email": "person@example.com",
                                          "telephone": "+44 0000"}}],
            "tender": tender}


def page(*rows):
    return {"releases": list(rows), "links": {}, "license": LICENSE,
            "publisher": {"name": "Cabinet Office"}}


class FakeDB:
    def __init__(self, role=SAFE_ROLE, fail_on=None):
        self.notices, self.versions, self.receipts = {}, [], []
        self.role, self.fail_on, self.sql = role, fail_on, []

    def state(self):
        return copy.deepcopy((self.notices, self.versions, self.receipts))

    def connect(self, *a, **kw):
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
        self.db, self.rows = db, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def _notice(self, nid):
        return next(n for n in self.db.notices.values() if n["id"] == nid)

    def execute(self, sql, args=None):
        db = self.db
        db.sql.append(sql)
        if db.fail_on and db.fail_on in sql:
            raise RuntimeError("forced_failure")
        self.rows = []
        if sql.startswith("SELECT current_user"):
            self.rows = [("bohun_uk_ingest_login",)]
        elif sql.startswith("SELECT r.rolsuper"):
            self.rows = [db.role]
        elif sql == w2.FIND_NOTICE:
            n = db.notices.get(args[0])
            self.rows = [(n["id"], n["latest_release_date"], n["completeness"])] if n else []
        elif sql == w2.FIND_VERSIONS:
            self.rows = [(v["source_record_sha256"],) for v in db.versions
                         if (v["ocid"], v["release_id"]) == tuple(args)]
        elif sql.startswith("INSERT INTO bohun_uk_intake.notices"):
            cols = re.search(r"\((.*?)\) VALUES", sql).group(1).split(",")
            row = dict(zip(cols, args))
            if row["ocid"] in db.notices:
                raise RuntimeError("unique_violation notices_ocid_key")
            row.update(id=len(db.notices) + 1, seen=1, changed=0)
            db.notices[row["ocid"]] = row
            self.rows = [(row["id"],)]
        elif sql.startswith("UPDATE bohun_uk_intake.notices SET latest_release_id"):
            cols = re.findall(r"(\w+)=%s", sql)
            n = self._notice(args[-1])
            n.update(dict(zip(cols, args[:-1])))
            n["seen"] += 1
            n["changed"] += 1
        elif sql in (w2.SEEN, w2.MARK_TENDER):
            n = self._notice(args[0])
            n["seen"] += 1
            if sql == w2.MARK_TENDER:
                n["completeness"] = "HAS_TENDER_NOTICE"
        elif sql == w2.INSERT_VERSION:
            keys = ("notice_id", "ocid", "release_id", "release_tag", "release_date",
                    "source_record_sha256", "release_json", "package_license",
                    "package_publisher", "applied_to_current", "reason")
            v = dict(zip(keys, args))
            if any((x["ocid"], x["release_id"], x["source_record_sha256"]) ==
                   (v["ocid"], v["release_id"], v["source_record_sha256"]) for x in db.versions):
                raise RuntimeError("unique_violation uk_version_unique")
            if any("contactPoint" in p for p in v["release_json"].get("parties", [])):
                raise RuntimeError("check_violation contactPoint")
            db.versions.append(copy.deepcopy(v))
        elif sql == w2.RECEIPT:
            db.receipts.append(args)
            self.rows = [(f"receipt-{len(db.receipts)}",)]
        elif sql.startswith(("SELECT pg_advisory_xact_lock", "SET LOCAL")):
            pass
        else:
            raise AssertionError("unexpected SQL: " + sql[:70])


def run(db, *rows, start=T0, dry_run=False):
    window = uk_window.prepare_window([page(*rows)], verified_complete=True)
    with patch.dict(os.environ, ENV):
        return w2.write_window(window, window_start=start, window_end=start + timedelta(hours=1),
                               connect=db.connect, dry_run=dry_run)


class FieldTests(unittest.TestCase):
    def test_sanitize_removes_contacts_without_mutating_input(self):
        r = rel()
        clean = uk_fields.sanitize(r)
        self.assertNotIn("contactPoint", json.dumps(clean))
        self.assertNotIn("@", json.dumps(clean))
        self.assertIn("contactPoint", r["parties"][0])

    def test_extract_full(self):
        f = uk_fields.extract(rel())
        self.assertEqual(f["cpv_codes"], ["35613000"])
        self.assertEqual(f["value_currency"], "GBP")
        self.assertEqual(f["tender_deadline"], datetime(2026, 11, 12, 12, tzinfo=timezone.utc))
        self.assertEqual(f["buyer_country"], "United Kingdom")
        self.assertTrue(f["notice_url"].startswith(uk_fields.NOTICE_URL_PREFIX))

    def test_partial_has_no_value_or_deadline(self):
        f = uk_fields.extract(rel(partial=True))
        self.assertFalse({"value_amount", "tender_deadline", "tender_status"} & set(f))

    def test_date_only_deadline_kept_raw(self):
        f = uk_fields.extract(rel(deadline="2026-10-26"))
        self.assertNotIn("tender_deadline", f)
        self.assertEqual(f["tender_deadline_raw"], "2026-10-26")

    def test_foreign_notice_url_ignored(self):
        r = rel()
        r["tender"]["documents"][0]["url"] = "https://example.com/Notice/1"
        self.assertNotIn("notice_url", uk_fields.extract(r))

    def test_bad_cpv_stops(self):
        r = rel(extra={"classification": {"scheme": "CPV", "id": "12-AB"}})
        with self.assertRaises(uk_fields.FieldError):
            uk_fields.extract(r)

    def test_unknown_sql_column_refused(self):
        with self.assertRaises(w2.StagingError):
            w2.insert_notice_sql(["title", "qa_status"])


class WindowTests(unittest.TestCase):
    def test_award_and_planning_skipped(self):
        w = uk_window.prepare_window([page(rel(), rel(rid="2", tag="award"), rel(rid="3", tag="planning"))],
                                     verified_complete=True)
        self.assertEqual(len(w["releases"]), 1)
        self.assertEqual(w["kinds"], {"other": 2, "tender": 1})

    def test_conflicting_same_release_stops(self):
        with self.assertRaises(uk_window.WindowError):
            uk_window.prepare_window([page(rel(), rel(title="Changed"))], verified_complete=True)

    def test_stage1_limit(self):
        rows = [rel(ocid=f"ocds-x-{i}", rid=f"{i:06d}-2026") for i in range(51)]
        with self.assertRaisesRegex(uk_window.WindowError, "release_limit"):
            uk_window.prepare_window([page(*rows)], verified_complete=True)

    def test_missing_license_stops(self):
        p = page(rel())
        del p["license"]
        with self.assertRaises(uk_window.WindowError):
            uk_window.prepare_window([p], verified_complete=True)

    def test_ordered_by_date(self):
        w = uk_window.prepare_window([page(rel(rid="b", minute=40), rel(ocid="o2", rid="a", minute=10))],
                                     verified_complete=True)
        self.assertEqual([r.release_id for r in w["releases"]], ["a", "b"])


class WriterTests(unittest.TestCase):
    def test_tender_then_full_update_is_one_notice_two_versions(self):
        db = FakeDB()
        x = run(db, rel(minute=5), rel(rid="000002-2026", tag="tenderUpdate", minute=40, amount=250.0))
        self.assertEqual(x["counts"], {"inserted": 1, "updated": 1, "repeated": 0, "late": 0, "versions": 2})
        n = db.notices["ocds-x-1"]
        self.assertEqual((n["value_amount"], n["latest_release_id"], n["completeness"]),
                         (250.0, "000002-2026", "HAS_TENDER_NOTICE"))
        self.assertEqual(len(db.versions), 2)

    def test_partial_update_keeps_fields(self):
        db = FakeDB()
        run(db, rel(minute=5), rel(rid="000002-2026", tag="tenderUpdate", minute=40, partial=True,
                                   title="Renamed"))
        n = db.notices["ocds-x-1"]
        self.assertEqual((n["title"], n["value_amount"], n["tender_status"]), ("Renamed", 100.0, "active"))
        self.assertEqual(n["tender_deadline"], datetime(2026, 11, 12, 12, tzinfo=timezone.utc))

    def test_update_before_tender_then_late_tender(self):
        db = FakeDB()
        run(db, rel(rid="000002-2026", tag="tenderUpdate", minute=40, partial=True, title="Update"))
        self.assertEqual(db.notices["ocds-x-1"]["completeness"], "UPDATE_ONLY")
        x = run(db, rel(minute=5, title="Original"), start=T0)
        n = db.notices["ocds-x-1"]
        self.assertEqual(x["counts"]["late"], 1)
        self.assertEqual((n["title"], n["completeness"], n["latest_release_id"]),
                         ("Update", "HAS_TENDER_NOTICE", "000002-2026"))
        self.assertEqual([v["applied_to_current"] for v in db.versions], [True, False])

    def test_rerun_has_no_duplicates(self):
        db = FakeDB()
        rows = (rel(minute=5), rel(rid="000002-2026", tag="tenderUpdate", minute=40))
        run(db, *rows)
        x = run(db, *rows)
        self.assertEqual(x["counts"], {"inserted": 0, "updated": 0, "repeated": 2, "late": 0, "versions": 0})
        self.assertEqual((len(db.notices), len(db.versions), len(db.receipts)), (1, 2, 2))

    def test_republished_release_is_new_version(self):
        db = FakeDB()
        run(db, rel(minute=5))
        x = run(db, rel(minute=5, title="Corrected title"))
        self.assertEqual(x["counts"]["versions"], 1)
        self.assertEqual(db.versions[-1]["reason"], "SOURCE_REPUBLISHED")
        self.assertEqual(db.notices["ocds-x-1"]["title"], "Corrected title")

    def test_stored_versions_have_no_contacts(self):
        db = FakeDB()
        run(db, rel())
        stored = json.dumps(db.versions[0]["release_json"])
        self.assertNotIn("contactPoint", stored)
        self.assertNotIn("person@", stored)
        self.assertEqual(db.versions[0]["package_license"], LICENSE)

    def test_dry_run_leaves_nothing(self):
        db = FakeDB()
        x = run(db, rel(), dry_run=True)
        self.assertEqual((x["committed"], x["counts"]["inserted"]), (False, 1))
        self.assertEqual(db.state(), ({}, [], []))

    def test_failure_rolls_back_everything(self):
        db = FakeDB(fail_on="INSERT INTO bohun_uk_intake.run_receipts")
        with self.assertRaises(RuntimeError):
            run(db, rel(), rel(rid="000002-2026", tag="tenderUpdate", minute=40))
        self.assertEqual(db.state(), ({}, [], []))

    def test_broad_privileges_stop_before_writes(self):
        db = FakeDB(role=(True,) + SAFE_ROLE[1:])
        with self.assertRaisesRegex(w2.StagingError, "privileges_not_minimal"):
            run(db, rel())
        self.assertFalse(any(s.startswith(("INSERT", "UPDATE")) for s in db.sql))

    def test_release_outside_receipt_window_stops(self):
        with self.assertRaisesRegex(w2.StagingError, "outside_receipt_window"):
            run(FakeDB(), rel(), start=T0 + timedelta(hours=1))

    def test_no_intelligence_or_checkpoint_sql(self):
        db = FakeDB()
        run(db, rel())
        self.assertFalse(any(("intelligence." in s or "checkpoints" in s) and "has_" not in s
                             for s in db.sql))

    def test_receipt_counts(self):
        db = FakeDB()
        run(db, rel(minute=5), rel(rid="000002-2026", tag="tenderUpdate", minute=40),
            rel(ocid="ocds-x-2", rid="000003-2026", minute=50, tag="award"))
        self.assertEqual(db.receipts[0][2:], (3, 2, 1, 1, 0, 1, 1, 2))


@unittest.skipUnless(os.environ.get("BOHUN_UK_REAL_PAGE"), "set BOHUN_UK_REAL_PAGE to a saved read-only page")
class RealPageTests(unittest.TestCase):
    def test_ac2_real_page(self):
        raw = json.loads(Path(os.environ["BOHUN_UK_REAL_PAGE"]).read_text("utf-8"))
        db = FakeDB()
        window = uk_window.prepare_window([raw], verified_complete=True)
        with patch.dict(os.environ, ENV):
            x = w2.write_window(window, window_start=T0, window_end=T0 + timedelta(hours=1),
                                connect=db.connect, dry_run=False)
        self.assertEqual((len(db.notices), len(db.versions)), (14, 15))
        self.assertEqual(x["counts"]["versions"], 15)
        n = db.notices["ocds-h6vhtk-06ac80"]
        self.assertEqual(n["latest_release_id"], "097293-2026")
        self.assertEqual(sum(v["ocid"] == "ocds-h6vhtk-06ac80" for v in db.versions), 2)
        only_updates = {r.ocid for r in window["releases"]} - {r.ocid for r in window["releases"] if r.tag == "tender"}
        self.assertEqual({o for o, n in db.notices.items() if n["completeness"] == "UPDATE_ONLY"}, only_updates)
        self.assertIn("ocds-h6vhtk-078330", only_updates)
        self.assertFalse(any("contactPoint" in json.dumps(v["release_json"]) for v in db.versions))
        with patch.dict(os.environ, ENV):
            again = w2.write_window(window, window_start=T0, window_end=T0 + timedelta(hours=1),
                                    connect=db.connect, dry_run=False)
        self.assertEqual(again["counts"], {"inserted": 0, "updated": 0, "repeated": 15, "late": 0, "versions": 0})


if __name__ == "__main__":
    unittest.main()
