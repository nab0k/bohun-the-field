"""Isolated fake DB tests. Never connect to Supabase or Find a Tender."""
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uk_staging_writer as w

START=datetime(2026,10,15,9,0,tzinfo=timezone.utc)
END=START+timedelta(hours=1)
A="a"*64
B="b"*64


def candidate(digest=A, tags=None, **overrides):
    tags = tags or ["tender"]
    ocid, rid="ocds-h6vhtk-123abc", "000123-2026"
    kw=dict(external_id=ocid+'::'+rid,title="Official tender example (synthetic)",
            published_at=START,record_sha256=digest,item_type="tender",url=None,
            raw_data={"bohun":{"qa":"HOLD","verification_status":"UNVERIFIED",
                                "public_release":False,"capture_status":"METADATA_ONLY",
                                "ready_for_import":False,"current_procurement_status":"unknown"},
                      "uk_fts":{"ocid":ocid,"release_id":rid,
                                "source_api":w.SOURCE_API,"tags":tags,
                                "source_record_sha256":digest,"release_date_raw":"2026-10-15T09:20:00Z"}})
    kw.update(overrides)
    return SimpleNamespace(**kw)


def manifest(candidates=None):
    candidates=candidates or [candidate()]
    return {"source":"uk_fts","qa":"HOLD","public_release":False,
            "source_window_complete":True,"production_write_approved":False,
            "pilot_candidates":len(candidates),"eligible_unique_tender_notices":len(candidates),
            "selection_limited":False,"observations":42,"candidates":candidates}


class FakeCursor:
    def __init__(self, existing=None, fail_on=None):
        self.existing=existing
        self.fail_on=fail_on
        self.last=None
        self.sql=[]
        self.writes=0
    def execute(self, sql, args=None):
        self.sql.append((sql,args))
        if self.fail_on and self.fail_on in sql:
            raise ValueError("forced_failure")
        if sql.startswith("SELECT current_user"):
            self.last=("bohun_uk_login",)
        elif sql.startswith("SELECT id,ocid"):
            self.last=self.existing
        elif sql.startswith("INSERT INTO bohun_uk_intake.notices"):
            self.last=(1001,)
            self.writes+=1
        elif sql.startswith("INSERT INTO bohun_uk_intake.run_receipts"):
            self.last=("test-uuid",)
            self.writes+=1
        elif sql.startswith(("UPDATE bohun_uk_intake", "INSERT INTO bohun_uk_intake.notice_versions")):
            self.writes+=1
        else:
            self.last=None
    def fetchone(self):
        return self.last
    def __enter__(self): return self
    def __exit__(self, *a): return False

class FakeConnection:
    def __init__(self, cursor):
        self.cur=cursor
        self.committed=False
        self.rolled_back=False
    def cursor(self): return self.cur
    @contextmanager
    def transaction(self):
        try: yield self
        except Exception:
            self.rolled_back=True
            raise
        else: self.committed=True
    def __enter__(self): return self
    def __exit__(self,*a): return False


class Tests(unittest.TestCase):
    def test_clean_manifest(self):
        self.assertEqual(len(w.validate_manifest(manifest(),window_start=START,window_end=END)),1)
    def test_invalid_public_gate(self):
        c=candidate(); c.raw_data['bohun']['public_release']=True
        with self.assertRaises(w.StagingError):w.validate_manifest(manifest([c]),window_start=START,window_end=END)
    def test_extra_metadata_blocked(self):
        c=candidate(); c.raw_data['private_source_body']={}
        with self.assertRaises(w.StagingError):w.validate_manifest(manifest([c]),window_start=START,window_end=END)
    def test_origin_mismatch_blocked(self):
        c=candidate(); c.external_id="changed"
        with self.assertRaises(w.StagingError):w.validate_manifest(manifest([c]),window_start=START,window_end=END)
    def test_more_than_ten(self):
        candidates=[candidate() for _ in range(11)]
        with self.assertRaises(w.StagingError):w.validate_manifest(manifest(candidates),window_start=START,window_end=END)
    def test_incomplete_window(self):
        m=manifest();m['source_window_complete']=False
        with self.assertRaises(w.StagingError):w.validate_manifest(m,window_start=START,window_end=END)
    def test_truncated_window(self):
        m=manifest();m['selection_limited']=True
        with self.assertRaises(w.StagingError):w.validate_manifest(m,window_start=START,window_end=END)
    def test_no_approval(self):
        old=dict(os.environ);os.environ.pop('BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF',None)
        try:
            with self.assertRaises(w.StagingError):w._approved()
        finally: os.environ.clear();os.environ.update(old)
    def test_first_insert(self):
        cursor=FakeCursor()
        c=w.validate_manifest(manifest(),window_start=START,window_end=END)
        x=w._apply_cursor(cursor,c,START,END,42,adapter=lambda v:v)
        self.assertEqual(x['counts'],{'inserted':1,'updated':0,'repeated':0})
        self.assertFalse(x['checkpoint_advanced'])
        self.assertEqual(cursor.writes,2)
        self.assertFalse(any('intelligence.items' in sql for sql,_ in cursor.sql))
    def test_identical_retry(self):
        cursor=FakeCursor((1001,'ocds-h6vhtk-123abc','000123-2026',A))
        c=w.validate_manifest(manifest(),window_start=START,window_end=END)
        x=w._apply_cursor(cursor,c,START,END,42,adapter=lambda v:v)
        self.assertEqual(x['counts']['repeated'],1)
        self.assertFalse(any('notice_versions' in sql for sql,_ in cursor.sql))
    def test_changed_version(self):
        cursor=FakeCursor((1001,'ocds-h6vhtk-123abc','000123-2026',A))
        c=w.validate_manifest(manifest([candidate(digest=B)]),window_start=START,window_end=END)
        x=w._apply_cursor(cursor,c,START,END,42,adapter=lambda v:v)
        self.assertEqual(x['counts']['updated'],1)
        self.assertTrue(any('notice_versions' in sql for sql,_ in cursor.sql))
    def test_identity_collision(self):
        cursor=FakeCursor((1001,'wrong-ocid','000123-2026',A))
        c=w.validate_manifest(manifest(),window_start=START,window_end=END)
        with self.assertRaises(w.StagingError):w._apply_cursor(cursor,c,START,END,42,adapter=lambda v:v)
    def test_rollback_on_failure(self):
        cur=FakeCursor(fail_on="INSERT INTO bohun_uk_intake.run_receipts")
        conn=FakeConnection(cur)
        c=w.validate_manifest(manifest(),window_start=START,window_end=END)
        with self.assertRaises(ValueError):
            with conn.transaction():
                w._apply_cursor(cur,c,START,END,42,adapter=lambda v:v)
        self.assertTrue(conn.rolled_back)
        self.assertFalse(conn.committed)
    def test_wrong_login_is_rejected(self):
        old=dict(os.environ)
        os.environ['BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF']='owner-approved:synthetic-test'
        os.environ['BOHUN_UK_STAGING_DSN']='synthetic'
        os.environ['BOHUN_UK_DB_WRITER_LOGIN']='postgres'
        cur=FakeCursor()
        conn=FakeConnection(cur)
        try:
            with self.assertRaises(w.StagingError):
                w.write_reviewed_pilot(manifest(),window_start=START,window_end=END,
                                       connect=lambda *args,**kw:conn)
        finally:os.environ.clear();os.environ.update(old)
        self.assertTrue(conn.rolled_back)

if __name__=='__main__': unittest.main()
