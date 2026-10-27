"""Real PostgreSQL transaction tests. ONLY a disposable local Unix-socket DB.

Set BOHUN_NER_TEST_DSN, dbname=bohun_ner_test, host=/.../bohun-ner-pg-*/socket.
No production/network DSN is accepted; no model calls are made.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import unittest
import uuid

from collectors.company_ner.contracts import CONTRACT_HASH, encode
from collectors.company_ner.store import Store, DatabaseUnknown
from collectors.company_ner.worker import run
from tests.test_company_ner import row, answer, catalog

DSN=os.environ.get('BOHUN_NER_TEST_DSN')


@unittest.skipUnless(DSN,'disposable local PostgreSQL DSN not supplied')
class PostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        from psycopg.rows import dict_row
        params=conninfo_to_dict(DSN);host=Path(params.get('host',''))
        if not host.is_absolute() or host.name!='socket' or not host.parent.name.startswith('bohun-ner-pg-') or params.get('dbname')!='bohun_ner_test':
            raise RuntimeError('LOCAL_DISPOSABLE_DB_REQUIRED')
        if host.parent.stat().st_uid!=os.getuid() or host.parent.stat().st_mode & 0o077:
            raise RuntimeError('PRIVATE_TEST_ROOT_REQUIRED')
        cls.factory=staticmethod(lambda: psycopg.connect(DSN,row_factory=dict_row))
        cls.psycopg=psycopg
        with cls.factory() as c:
            assert c.execute('SELECT current_database() db,inet_server_addr() addr').fetchone()=={'db':'bohun_ner_test','addr':None}
            c.execute('CREATE SCHEMA intelligence; CREATE SCHEMA companies; CREATE SCHEMA exp009')
            c.execute('''CREATE TABLE intelligence.items(id bigint PRIMARY KEY,title text,url text,
              item_type text,published_at timestamptz,updated_at timestamptz,raw_data jsonb);
              CREATE TABLE companies.company(id text PRIMARY KEY,name text,country text,status text,
                notion_page_id text,updated_at timestamptz);
              CREATE TABLE companies.company_alias(company_id text,alias text)''')
        # Candidate SQL contains BEGIN/COMMIT; apply only after the local-DB guard.
        with psycopg.connect(DSN,autocommit=True) as c:
            c.execute((Path(__file__).resolve().parents[1]/'db/companies/company_ner_storage_candidate.sql').read_text())

    def setUp(self):
        with self.factory() as c:
            c.execute('TRUNCATE exp009.company_ner_links,exp009.company_ner_inputs,exp009.company_ner_cache,intelligence.items,companies.company,companies.company_alias')
            x=catalog()[0]
            c.execute('INSERT INTO companies.company VALUES(%s,%s,%s,%s,%s,%s)',
                      tuple(x[k] for k in ['id','name','country','status','notion_page_id','updated_at']))
        self.store=Store(self.factory);self.calls=0

    def seed(self,r):
        with self.factory() as c:
            c.execute('INSERT INTO intelligence.items VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)',
              (int(r['item_id']),r['title'],r['url'],'news',r['published_at'],r['source_updated_at'],encode({'content_capture':r['content_capture']})))

    def model(self,text):
        self.calls+=1;return answer(text)

    def count(self,table):
        # table name is test-owned, never a source value.
        with self.factory() as c:return c.execute('SELECT count(*) n FROM '+table).fetchone()['n']

    def test_duplicates_and_restart_need_one_inference(self):
        self.seed(row(ident='1'));self.seed(row(ident='2'))
        first=run(self.store,self.model)
        self.assertEqual((first['completed'],first['linked_sources'],first['proposed_links']),(1,2,2))
        second=run(Store(self.factory),self.model)
        self.assertEqual(self.calls,1);self.assertEqual(second['model_calls'],0)
        self.assertEqual(self.count('exp009.company_ner_inputs'),2)
        self.assertEqual(self.count('exp009.company_ner_cache'),1)

    def test_failure_held_without_automatic_model_retry(self):
        self.seed(row())
        def failing(text):self.calls+=1;raise RuntimeError('SECRET_SOURCE_NEVER_LOGGED')
        result=run(self.store,failing)
        self.assertEqual(result['inference_held'],1)
        self.assertEqual(run(Store(self.factory),failing)['model_calls'],0);self.assertEqual(self.calls,1)

    def test_native_failure_category_is_retained_without_raw_exception(self):
        from collectors.company_ner.native import NativeError
        self.seed(row())
        def timeout(text):raise NativeError('NER_TIME_CAP')
        run(self.store,timeout)
        with self.factory() as c:
            state=c.execute('SELECT state,hold_reason FROM exp009.company_ner_cache').fetchone()
        self.assertEqual(state,{'state':'hold','hold_reason':'NER_TIME_CAP'})

    def test_invalid_source_cannot_starve_valid_item(self):
        bad=row();bad['content_capture']['text_sha256']='0'*64
        self.seed(bad);self.seed(row(ident='2'))
        result=run(self.store,self.model)
        self.assertEqual((result['intake_held'],result['completed']),(1,1))
        self.assertEqual(run(self.store,self.model)['ingested'],0)

    def test_concurrent_claims_are_distinct(self):
        for ident in ['1','2']:
            self.seed(row(('Acme text '+ident+'. ')*25,ident))
        for r in self.store.pending_sources(10):self.store.enqueue(r)
        with ThreadPoolExecutor(max_workers=2) as ex:
            jobs=list(ex.map(lambda _: Store(self.factory).claim(),range(2)))
        self.assertEqual(len({j['job_key'] for j in jobs}),2)
        self.assertIsNone(self.store.claim())

    def test_abandoned_job_held_not_requeued(self):
        self.seed(row());self.store.enqueue(self.store.pending_sources(1)[0]);self.store.claim()
        with self.factory() as c:c.execute("UPDATE exp009.company_ner_cache SET started_at=clock_timestamp()-interval '11 minutes'")
        result=run(Store(self.factory),self.model)
        self.assertEqual((result['abandoned_held'],result['model_calls']),(1,0))

    def test_stale_token_cannot_finish(self):
        self.seed(row());self.store.enqueue(self.store.pending_sources(1)[0]);j=self.store.claim()
        with self.assertRaisesRegex(ValueError,'STALE_NER_CLAIM'):
            self.store.finish({**j,'token':str(uuid.uuid4())},answer(j['body']))

    def test_catalog_change_relinks_without_inference(self):
        self.seed(row());run(self.store,self.model)
        with self.factory() as c:c.execute("INSERT INTO companies.company VALUES('co_two','Acme PLC','GB','candidate',NULL,clock_timestamp())")
        result=run(Store(self.factory),self.model)
        self.assertEqual((result['model_calls'],result['proposed_links']),(0,2))
        self.assertEqual(self.count('exp009.company_ner_links'),2)
        with self.factory() as c:
            packets=c.execute('SELECT packet FROM exp009.company_ner_links').fetchall()
        self.assertTrue(all(not x['packet']['confirmed_links'] for x in packets))

    def test_source_change_creates_version_and_new_computation(self):
        self.seed(row());run(self.store,self.model)
        newer=row(('Acme revised the announcement. ')*12)
        with self.factory() as c:c.execute("UPDATE intelligence.items SET raw_data=%s::jsonb,updated_at=updated_at+interval '1 minute'",(encode({'content_capture':newer['content_capture']}),))
        run(Store(self.factory),self.model)
        self.assertEqual((self.calls,self.count('exp009.company_ner_inputs')),(2,2))

    def test_invalid_model_spans_held(self):
        self.seed(row())
        def broken(text):
            value=answer(text);value['entities']['organization'][0]['end']=2;return value
        result=run(self.store,broken);self.assertEqual(result['inference_held'],1)
        self.assertEqual(run(self.store,self.model)['model_calls'],0)

    def test_unknown_finish_commit_recovers_without_model_repeat(self):
        self.seed(row());real_finish=self.store.finish
        def uncertain(job,output):
            real_finish(job,output)
            raise DatabaseUnknown('SIMULATED_LOST_ACK_AFTER_SERVER_COMMIT')
        self.store.finish=uncertain
        with self.assertRaises(DatabaseUnknown):run(self.store,self.model)
        result=run(Store(self.factory),self.model)
        self.assertEqual((self.calls,result['model_calls'],result['linked_sources']),(1,0,1))

    def test_unknown_claim_commit_does_not_dispatch_model(self):
        self.seed(row());real_claim=self.store.claim
        def uncertain():real_claim();raise DatabaseUnknown('SIMULATED_LOST_CLAIM_ACK')
        self.store.claim=uncertain
        with self.assertRaises(DatabaseUnknown):run(self.store,self.model)
        self.assertEqual(self.calls,0);self.assertIsNone(Store(self.factory).claim())

    def test_store_tx_reports_real_commit_lost_ack_and_keeps_saved_output(self):
        self.seed(row());self.store.enqueue(self.store.pending_sources(1)[0]);job=self.store.claim()
        factory=self.factory
        class LostAck:
            def __init__(self):self.conn=factory()
            @property
            def autocommit(self):return self.conn.autocommit
            def execute(self,*args):return self.conn.execute(*args)
            def commit(self):
                self.conn.commit()
                raise ConnectionError('SERVER_COMMITTED_ACK_LOST')
            def close(self):self.conn.close()
        with self.assertRaisesRegex(DatabaseUnknown,'NER_COMMIT_UNKNOWN'):
            Store(LostAck).finish(job,answer(job['body']))
        result=run(Store(factory),self.model)
        self.assertEqual((result['model_calls'],result['linked_sources']),(0,1))

    def test_storage_rejects_packet_missing_hold_fields(self):
        self.seed(row());run(self.store,self.model)
        with self.factory() as c:
            c.execute('SAVEPOINT malformed_packet')
            with self.assertRaises(self.psycopg.errors.CheckViolation):
                c.execute("UPDATE exp009.company_ner_links SET packet='{}'::jsonb")
            c.execute('ROLLBACK TO SAVEPOINT malformed_packet')

    def test_rls_prevents_read_even_after_select_grant(self):
        self.seed(row());run(self.store,self.model)
        with self.factory() as c:
            c.execute('CREATE ROLE ner_api_test NOLOGIN')
            c.execute('GRANT USAGE ON SCHEMA exp009 TO ner_api_test')
            c.execute('GRANT SELECT ON exp009.company_ner_cache,exp009.company_ner_inputs,exp009.company_ner_links TO ner_api_test')
            c.execute('SET LOCAL ROLE ner_api_test')
            self.assertEqual(c.execute('SELECT count(*) n FROM exp009.company_ner_cache').fetchone()['n'],0)


if __name__=='__main__':unittest.main()
