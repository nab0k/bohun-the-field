import os
import unittest
from unittest.mock import patch

from test_uk_pilot import prepare,row
from uk_hold_apply import execute_one_transaction

class FakeCursor:
    def __init__(self,existing):
        self.existing=existing
        self.queries=[]
        self.last=''
        self.changes=[]
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def execute(self,sql,params=()):
        self.queries.append((sql,params))
        if 'SELECT id FROM intelligence.sources' in sql:self.last='source'
        elif 'SELECT id,raw_data FROM intelligence.items' in sql:self.last='existing'
        elif 'INSERT INTO intelligence.items' in sql:self.last='insert'
        else:self.last='nothing'
        if 'INSERT INTO intelligence.item_changes' in sql:self.changes.append(params)
    def fetchone(self):
        if self.last=='source':return (27,)
        if self.last=='existing':return self.existing
        if self.last=='insert':return (1001,)
        return None

class FakeTransaction:
    def __init__(self):self.exited=False;self.failed=False
    def __enter__(self):return self
    def __exit__(self,t,v,tb):self.exited=True;self.failed=t is not None;return False

class FakeConnection:
    def __init__(self,existing):
        self.cur=FakeCursor(existing)
        self.tx=FakeTransaction()
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):return self.tx
    def cursor(self):return self.cur

class WriterTests(unittest.TestCase):
    def setUp(self):
        self.c=prepare(row())['candidates'][0]
        self.env=patch.dict(os.environ,{'BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF':'owner-approved:synthetic-unit-test',
            'BOHUN_UK_HOLD_READERS_AUDIT_REF':'reviewed:synthetic-unit-test',
            'SUPABASE_DB_PASSWORD':'synthetic-not-a-secret'})
        self.env.start()
    def tearDown(self):self.env.stop()
    def test_insert_sql_cannot_publish(self):
        conn=FakeConnection(None)
        out=execute_one_transaction([self.c],connect=lambda **kw:conn)
        self.assertEqual(out['committed_counts']['insert_hold'],1)
        self.assertFalse(out['public_release'])
        self.assertTrue(conn.tx.exited)
        sql='\n'.join(x[0] for x in conn.cur.queries)
        self.assertIn('INSERT INTO intelligence.items',sql)
        self.assertNotIn('INSERT INTO intelligence.opportunities',sql)
        self.assertNotIn('UPDATE exp009.',sql)
    def test_repeat_only_updates_seen(self):
        conn=FakeConnection((100,{'uk_fts':{'source_record_sha256':self.c.record_sha256}}))
        out=execute_one_transaction([self.c],connect=lambda **kw:conn)
        self.assertEqual(out['committed_counts']['refresh_seen_only'],1)
        self.assertEqual(conn.cur.changes,[])
    def test_changed_record_appends_history(self):
        conn=FakeConnection((100,{'uk_fts':{'source_record_sha256':'deadbeef'}}))
        out=execute_one_transaction([self.c],connect=lambda **kw:conn)
        self.assertEqual(out['committed_counts']['new_metadata_version_review'],1)
        self.assertEqual(len(conn.cur.changes),1)
    def test_unsafe_provenance_rolls_back(self):
        conn=FakeConnection((100,{}))
        with self.assertRaisesRegex(Exception,'provenance_mismatch'):
            execute_one_transaction([self.c],connect=lambda **kw:conn)
        self.assertTrue(conn.tx.exited)
        self.assertTrue(conn.tx.failed)
    def test_max_ten(self):
        conn=FakeConnection(None)
        with self.assertRaisesRegex(Exception,'pilot_candidate_count'):
            execute_one_transaction([self.c]*11,connect=lambda **kw:conn)
        self.assertFalse(conn.tx.exited)
    def test_source_scoped_advisory_lock(self):
        conn=FakeConnection(None)
        execute_one_transaction([self.c],connect=lambda **kw:conn)
        self.assertTrue(conn.cur.queries[0][0].startswith('SELECT pg_advisory_xact_lock'))

if __name__=='__main__':unittest.main()
