"""Offline fake-database regression. Never uses network, credentials or Supabase."""
import copy
import hashlib
import json
import os
import re
import sys
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace, ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if not (ROOT / 'r8_source_reader.py').exists():
    sys.path.insert(0, str(ROOT / 'dependencies'))
from r8_source_reader import make_window, read_window, API, WindowError
import r9_batch_plan as planner
import r9_staging_writer as writer

WINDOW = make_window('2026-10-15T09:00:00', '2026-10-15T10:00:00')
ENV = {'BOHUN_UK_R9_BATCH_APPROVAL_REF':'owner-approved:offline-synthetic',
       'BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF':'owner-approved:offline-synthetic',
       'BOHUN_UK_STAGING_DSN':'synthetic',
       'BOHUN_UK_DB_WRITER_LOGIN':'bohun_uk_ingest_login'}


def release(n, *, tag='tender', title=None, date='2020-01-01T00:00:00Z'):
    return {'ocid': f'ocds-h6vhtk-test-{n:06d}', 'id': f'{n}-r', 'date':date,
            'tag': [tag], 'tender': {'title': title or f'Synthetic item {n}'}}


def norm(pages, *, verified_complete, max_tenders=10):
    assert verified_complete
    rows=[r for p in pages for r in p['releases']]
    tender=[r for r in rows if 'tender' in r['tag']]
    by_id={}
    for r in tender:
        external=r['ocid']+'::'+r['id']
        digest=hashlib.sha256(json.dumps(r, sort_keys=True).encode()).hexdigest()
        by_id[external]=SimpleNamespace(
            external_id=external, title=r['tender']['title'], published_at=datetime(2020,1,1,tzinfo=timezone.utc),
            record_sha256=digest,raw_data={'bohun':{'qa':'HOLD','verification_status':'UNVERIFIED',
            'public_release':False,'ready_for_import':False},'uk_fts':{'ocid':r['ocid'],
            'release_id':r['id'],'source_record_sha256':digest}},item_type='tender')
    return {'source':'uk_fts','qa':'HOLD','public_release':False,
            'source_window_complete':True,'selection_limited':len(by_id)>max_tenders,
            'eligible_unique_tender_notices':len(by_id),'pilot_candidates':min(len(by_id),max_tenders),
            'observations':len(rows),'candidates':list(by_id.values())[:max_tenders]}


def read(*rows, duplicate=0):
    return {'complete':True,'window':WINDOW,'pages':1,'unique_releases':len(rows),
            'duplicate_releases':duplicate,'releases':list(rows)}


class FakeDB:
    def __init__(self, fail_sql=None, role='bohun_uk_ingest_login'):
        self.notices={};self.versions=[];self.receipts=[];self.queries=[]
        self.fail_sql=fail_sql;self.role=role

    def state(self):return copy.deepcopy((self.notices,self.versions,self.receipts))
    def connect(self,*a,**k):return FakeConn(self)

class FakeConn:
    def __init__(self,db):self.db=db
    def __enter__(self):return self
    def __exit__(self,*a):return False
    @contextmanager
    def transaction(self):
        old=self.db.state()
        try:yield self
        except BaseException:
            self.db.notices,self.db.versions,self.db.receipts=old
            raise
    def cursor(self):return FakeCur(self.db)

class FakeCur:
    def __init__(self,db):self.db=db;self.row=None
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def fetchone(self):return self.row
    def execute(self,sql,args=None):
        self.db.queries.append(sql)
        if self.db.fail_sql and self.db.fail_sql in sql:raise RuntimeError('synthetic-failure')
        self.row=None
        if sql=='SELECT current_user':self.row=(self.db.role,)
        elif sql.startswith('SELECT id,ocid'):
            n=self.db.notices.get(args[0]);self.row=(n['id'],n['ocid'],n['rid'],n['sha']) if n else None
        elif sql.startswith('INSERT INTO bohun_uk_intake.notices'):
            ext,ocid,rid,title,at,sha,tags,metadata=args
            if ext in self.db.notices:raise RuntimeError('unique_collision')
            self.db.notices[ext]={'id':len(self.db.notices)+1,'ocid':ocid,'rid':rid,'title':title,'sha':sha,'seen':1,'metadata':metadata}
            self.row=(self.db.notices[ext]['id'],)
        elif sql.startswith('UPDATE bohun_uk_intake.notices SET last_seen_at'):
            next(v for v in self.db.notices.values() if v['id']==args[0])['seen']+=1
        elif sql.startswith('UPDATE bohun_uk_intake.notices SET title'):
            n=next(v for v in self.db.notices.values() if v['id']==args[5]);n.update(title=args[0],sha=args[2],metadata=args[4],seen=n['seen']+1)
        elif sql.startswith('INSERT INTO bohun_uk_intake.notice_versions'):
            self.db.versions.append(args)
        elif sql.startswith('INSERT INTO bohun_uk_intake.run_receipts'):
            self.db.receipts.append(args);self.row=(f'fake-{len(self.db.receipts)}',)
        elif sql.startswith(('SELECT pg_advisory_xact_lock','SET LOCAL')):pass
        else:raise AssertionError(f'unexpected SQL: {sql[:100]}')


def fake_base():
    b=ModuleType('uk_staging_writer')
    b.HASH_RE=re.compile('[0-9a-f]{64}');b.LOCK_ID=1
    b.FIND='SELECT id,ocid,release_id,source_record_sha256 FROM bohun_uk_intake.notices WHERE external_id=%s FOR UPDATE'
    b.INSERT='INSERT INTO bohun_uk_intake.notices (...) VALUES (...) RETURNING id'
    b.SEEN='UPDATE bohun_uk_intake.notices SET last_seen_at=now() WHERE id=%s'
    b.UPDATE='UPDATE bohun_uk_intake.notices SET title=%s,published_at=%s,source_record_sha256=%s,tags=%s,metadata=%s,last_seen_at=now(),last_changed_at=now() WHERE id=%s'
    b.VERSION='INSERT INTO bohun_uk_intake.notice_versions (notice_id,old_record_sha256,new_record_sha256) VALUES (%s,%s,%s)'
    b.RECEIPT='INSERT INTO bohun_uk_intake.run_receipts (window_start,window_end,... ) VALUES (...) RETURNING id'
    b._approved=lambda: None
    b._dsn_without_secret=lambda:'synthetic'
    b._preflight=lambda cur: None
    def canonical(c):
        m=c.raw_data
        if m['bohun']['qa']!='HOLD' or m['bohun']['public_release'] is not False:
            raise ValueError('not_hold')
        ocid,rid=c.external_id.split('::')
        return (c.external_id,ocid,rid,c.title,c.published_at,c.record_sha256,['tender'],m)
    b._canonical_metadata=canonical
    return b

class PlannerTests(unittest.TestCase):
    def test_empty_window(self):
        batch=planner.prepare_batch(read(),norm)
        self.assertEqual((batch.tender_candidates,batch.observed_releases),(0,0))
    def test_awards_only(self):
        batch=planner.prepare_batch(read(*[release(i,tag='award') for i in range(29)]),norm)
        self.assertEqual((batch.tender_candidates,batch.observed_releases),(0,29))
    def test_seventeen_tenders_more_than_pilot(self):
        b=planner.prepare_batch(read(*[release(i) for i in range(17)]),norm)
        self.assertEqual(b.tender_candidates,17)
        self.assertEqual(len({c.external_id for c in b.candidates}),17)
    def test_second_page_and_many_releases(self):
        r=read(*[release(i,tag='award' if i%8 else 'tender') for i in range(205)])
        b=planner.prepare_batch(r,norm)
        self.assertEqual((b.tender_candidates,b.observed_releases),(26,205))
    def test_source_duplicate_receipt(self):
        b=planner.prepare_batch(read(release(1),duplicate=2),norm)
        self.assertEqual(b.observed_releases,3)
    def test_partial_source_denied(self):
        r=read(release(1));r['complete']=False
        with self.assertRaisesRegex(planner.BatchPlanError,'incomplete'):planner.prepare_batch(r,norm)
    def test_count_mismatch_denied(self):
        r=read(release(1));r['unique_releases']=2
        with self.assertRaises(planner.BatchPlanError):planner.prepare_batch(r,norm)
    def test_limit_denied_without_partial_write(self):
        with self.assertRaisesRegex(planner.BatchPlanError,'tender_count_over_cap'):
            planner.prepare_batch(read(*[release(i) for i in range(20)]),norm,max_tenders=15)
    def test_bad_window_denied(self):
        from r8_source_reader import SourceWindow
        bad = read(release(1))
        bad['window'] = SourceWindow(WINDOW.local_from,WINDOW.local_to,WINDOW.utc_to,WINDOW.utc_from)
        with self.assertRaisesRegex(planner.BatchPlanError,'window_untrusted'):
            planner.prepare_batch(bad,norm)
    def test_bad_normalizer_denied(self):
        def malicious(pages,**kwargs):
            d=norm(pages,**kwargs);d['public_release']=True;return d
        with self.assertRaises(planner.BatchPlanError):planner.prepare_batch(read(release(1)),malicious)
    def test_no_identity_collision_in_full_window(self):
        with self.assertRaisesRegex(planner.BatchPlanError,'duplicate_source_identity'):
            planner.prepare_batch(read(release(1),release(1)),norm)

class WriterTests(unittest.TestCase):
    @contextmanager
    def sandbox(self):
        with patch.dict(os.environ,ENV),patch.dict(sys.modules,{'uk_staging_writer':fake_base()}):yield
    def test_zero_tender_commit_one_receipt(self):
        db=FakeDB(); b=planner.prepare_batch(read(),norm)
        with self.sandbox():out=writer.write_batch(b,connect=db.connect,dry_run=False)
        self.assertEqual((out['candidate_count'],len(db.notices),len(db.receipts)),(0,0,1))
        self.assertEqual(db.receipts[0][2:4],(0,0))
    def test_awards_only_receipt(self):
        db=FakeDB(); b=planner.prepare_batch(read(release(1,tag='award'),release(2,tag='award')),norm)
        with self.sandbox():writer.write_batch(b,connect=db.connect,dry_run=False)
        self.assertEqual(db.receipts[0][2:4],(2,0))
    def test_many_tenders_atomic_then_repeat(self):
        db=FakeDB();b=planner.prepare_batch(read(*[release(i) for i in range(19)]),norm)
        with self.sandbox():
            first=writer.write_batch(b,connect=db.connect,dry_run=False)
            second=writer.write_batch(b,connect=db.connect,dry_run=False)
        self.assertEqual((first['counts']['inserted'],second['counts']['repeated'],len(db.notices),len(db.receipts)),(19,19,19,2))
        self.assertEqual(sum(db.receipts[0][-3:]),19)
    def test_old_publication_date_allowed(self):
        db=FakeDB();b=planner.prepare_batch(read(release(1,date='2003-01-01T00:00:00Z')),norm)
        with self.sandbox():writer.write_batch(b,connect=db.connect,dry_run=False)
        self.assertEqual(len(db.notices),1)
    def test_changed_release_creates_version(self):
        db=FakeDB();b1=planner.prepare_batch(read(release(1)),norm)
        b2=planner.prepare_batch(read(release(1,title='updated')),norm)
        with self.sandbox():
            writer.write_batch(b1,connect=db.connect,dry_run=False)
            o=writer.write_batch(b2,connect=db.connect,dry_run=False)
        self.assertEqual((o['counts']['updated'],len(db.versions)),(1,1))
    def test_rollback_on_receipt_failure(self):
        db=FakeDB(fail_sql='INSERT INTO bohun_uk_intake.run_receipts');b=planner.prepare_batch(read(*[release(i) for i in range(19)]),norm)
        with self.sandbox(),self.assertRaises(RuntimeError):
            writer.write_batch(b,connect=db.connect,dry_run=False)
        self.assertEqual(db.state(),({},[],[]))
    def test_dry_run_rollback(self):
        db=FakeDB();b=planner.prepare_batch(read(*[release(i) for i in range(19)]),norm)
        with self.sandbox():x=writer.write_batch(b,connect=db.connect,dry_run=True)
        self.assertFalse(x['committed']);self.assertEqual(db.state(),({},[],[]))
    def test_no_approval_fails_before_connection(self):
        db=FakeDB();b=planner.prepare_batch(read(),norm)
        with patch.dict(os.environ,{**ENV,'BOHUN_UK_R9_BATCH_APPROVAL_REF':''}),patch.dict(sys.modules,{'uk_staging_writer':fake_base()}):
            with self.assertRaises(writer.R9WriteError):writer.write_batch(b,connect=db.connect)
        self.assertEqual(db.queries,[])
    def test_wrong_login_fails_before_write(self):
        db=FakeDB(role='postgres');b=planner.prepare_batch(read(release(1)),norm)
        with self.sandbox(),self.assertRaises(writer.R9WriteError):writer.write_batch(b,connect=db.connect,dry_run=False)
        self.assertFalse(any(q.startswith('INSERT INTO') for q in db.queries))
    def test_no_checkpoint_sql(self):
        db=FakeDB();b=planner.prepare_batch(read(release(1)),norm)
        with self.sandbox():writer.write_batch(b,connect=db.connect,dry_run=False)
        self.assertFalse(any('checkpoints' in q for q in db.queries))

class R8IntegrationTests(unittest.TestCase):
    def test_actual_reader_three_pages_preserves_all_notices(self):
        sample=[release(i,tag='tender' if i%7==0 else 'award') for i in range(205)]
        cursor=['AAA','BBB']
        pages=[]
        for i in range(3):
            rows=sample[i*100:(i+1)*100]
            nxt=f'{API}?cursor={cursor[i]}' if i<2 else None
            pages.append(json.dumps({'releases':rows,'links': {'next':nxt} if nxt else {}}).encode())
        calls=[]
        def get(url):
            calls.append(url)
            return pages[len(calls)-1]
        snap=read_window(WINDOW,get)
        b=planner.prepare_batch(snap,norm)
        self.assertEqual((snap['pages'],snap['unique_releases'],b.tender_candidates),(3,205,30))
    def test_failed_read_cannot_form_batch(self):
        with self.assertRaises(WindowError):
            read_window(WINDOW,lambda u: b'broken json')

if __name__=='__main__':unittest.main()
