"""REAL database tests. Never selected by offline discovery; no skipped success."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass,replace,asdict
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import threading
import time
import unittest

from collectors.companies.automation import contracts as c,checkpoint as w
from collectors.companies.automation.changes import evaluate
from collectors.companies.automation.postgres import PostgresBackend,trigger_receipt
from collectors.companies.automation.worker import WorkContext,run_one,PreparedWork
from .environment import ROOT,HERE,cloud_guard,state_path
from .rpc_transport import Endpoint,RpcTransport,cloud_connect

POLICY=c.Policy()
CID='co_synthetic_rotor'


def now():return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Card:
    company:c.CompanySnapshot
    properties_json:str
    read_at:datetime
    complete:bool


class PostgresLive(unittest.TestCase):
    def setUp(self):
        cloud_guard()
        self.state=json.loads(state_path().read_text())
        self.endpoint=Endpoint('127.0.0.1',self.state['port'],'exp016_proof',
                               'bohun_companies_worker_v0',self.state['worker_password'])
        self.admin('DROP SCHEMA IF EXISTS exp016_fixture CASCADE; DROP SCHEMA IF EXISTS companies CASCADE; '
                   'DROP ROLE IF EXISTS bohun_companies_owner_v0; DROP ROLE IF EXISTS bohun_companies_worker_v0;')
        self.admin((ROOT/'db/companies/001_companies_schema.sql').read_text())
        self.admin((HERE/'bootstrap.sql').read_text())
        self.admin((ROOT/'db/companies/automation_v0_candidate.sql').read_text())
        # Credentials apply only to this disposable cluster. No role changes in Supabase.
        self.admin('ALTER ROLE bohun_companies_worker_v0 LOGIN PASSWORD '+self.literal(self.state['worker_password']))
        self.install_hooks()
        self.admin('UPDATE companies.automation_control SET enabled=true,mode=%s,policy_hash=%s,policy_json=%s::jsonb,'
                   'code_manifest=%s,scope_all=true',('private_write',POLICY.fingerprint,c.json_text(asdict(POLICY)),c.digest('fixture')))
        self.add_company(CID)

    def connect_admin(self):
        cloud_guard()
        import psycopg
        return psycopg.connect(host='127.0.0.1',port=self.state['port'],dbname='exp016_proof',user='postgres',
                              password=self.state['admin_password'],autocommit=True,connect_timeout=5,
                              options='-c statement_timeout=15000 -c lock_timeout=3000')

    def admin(self,sql,params=None):
        with self.connect_admin() as conn:
            cur=conn.execute(sql,params,prepare=False)
            return cur.fetchall() if cur.description else []

    @staticmethod
    def literal(value):
        # Temporary random hex passwords only; no general string interpolation.
        if not value or any(ch not in '0123456789abcdef' for ch in value):raise RuntimeError('FIXTURE_PASSWORD_INVALID')
        return "'"+value+"'"

    def transport(self,**kw):return RpcTransport(lambda:cloud_connect(self.endpoint),**kw)
    def port(self,**kw):return PostgresBackend(self.transport(),policy=POLICY,clock=now,card_factory=Card,**kw)

    def install_hooks(self):
        self.admin("""CREATE OR REPLACE FUNCTION companies.automation_profile_read_v0(ids text[]) RETURNS jsonb
          LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$SELECT exp016_fixture.profile_reader(ids)$$;
          CREATE OR REPLACE FUNCTION companies.automation_source_read_v0(cid text,ref text,url_value text,hash_value text)
          RETURNS jsonb LANGUAGE sql SECURITY DEFINER SET search_path='' AS $$
          SELECT exp016_fixture.source_reader(cid,ref,url_value,hash_value)$$;
          REVOKE ALL ON FUNCTION companies.automation_profile_read_v0(text[]),
          companies.automation_source_read_v0(text,text,text,text) FROM PUBLIC;
          GRANT EXECUTE ON FUNCTION companies.automation_profile_read_v0(text[]),
          companies.automation_source_read_v0(text,text,text,text) TO bohun_companies_owner_v0;""")

    def add_company(self,cid,country='DE',view=None):
        self.admin('INSERT INTO companies.company(id,name,name_norm,country) VALUES(%s,%s,%s,%s)',(cid,cid,cid,country))
        view=view or c.CompanyView(cid,country=country,fields=(('/identity/website',c.FieldValue(False,owner='machine')),))
        snapshot=c.CompanySnapshot(cid,c.digest(['synthetic-profile',cid]))
        ctx=WorkContext(view,c.digest(['input',cid]))
        self.admin('INSERT INTO exp016_fixture.profile(company_id,card,context,properties_json) VALUES(%s,%s::jsonb,%s::jsonb,%s)',
          (cid,c.json_text(w.encode(snapshot)),c.json_text(w.encode(ctx)),c.json_text({'Legal name':cid,'Country':country})))
        return ctx

    def job(self,cid=CID,section='IDENTITY',revision='initial'):
        return c.JobSpec('ENRICH_SECTION',POLICY.fingerprint,POLICY.version,c.digest(['input',cid]),cid,section,revision=revision)

    def enqueue(self,ids=(CID,),*,trigger=None):
        p=self.port(triggers=(trigger,) if trigger else ());rev,_,cp=p.read_current(ids)
        jobs=tuple(self.job(cid) for cid in ids)
        prepared={'jobs':jobs,'existing_keys':cp['existing_keys']|frozenset(j.job_key for j in jobs),
          'seen_triggers':cp['seen_triggers']|frozenset([trigger['key']] if trigger else []),
          'section_inputs':cp['section_inputs'],'profile_hashes':cp['profile_hashes'],
          'unresolved_companies':(),'checks':(),'external_calls':0,'persistent_write_performed':False,'assessments':{}}
        self.assertEqual(p.commit_batch(rev,prepared),'APPLIED');return p,jobs

    def change(self,p,lease):
        ctx=p.context(lease);captured=now()-timedelta(days=1)
        text='Synthetic Rotor states its website is https://rotor.example.test.'
        src=c.SourceDocument('synthetic-source','https://rotor.example.test/about',text,captured,'official_company')
        q=c.Qualifiers(as_of=captured.date().isoformat());value=c.json_text('https://rotor.example.test')
        ev=c.Evidence(src,src.content_hash,CID,'official_name_country_domain',0,len(text),text,q,'/identity/website',value)
        claim=c.ProposedClaim(CID,'IDENTITY','synthetic-claim','/identity/website',value,(ev,),q)
        change=evaluate(ctx.view,(claim,),section='IDENTITY',policy=POLICY,now=now())
        self.assertEqual(len(change.claims),1)
        self.admin('INSERT INTO companies.profile_source(company_id,source_ref,title,source_url,source_kind,as_of,'
          'original_bytes_archived,snapshot_sha256,captured_at) VALUES(%s,%s,%s,%s,%s,%s,true,%s,%s)',
          (CID,src.source_ref,'Synthetic source',src.url,src.kind,captured.date(),src.content_hash,captured))
        self.admin('INSERT INTO exp016_fixture.source(company_id,source_ref,url,text_hash,binding,verified) '
          'VALUES(%s,%s,%s,%s,%s,%s::jsonb)',(CID,src.source_ref,src.url,src.content_hash,ev.binding,c.json_text([claim.content_hash])))
        return change

    def pending(self):
        p,_=self.enqueue();lease=p.claim(POLICY.fingerprint,now(),300);return p,lease,self.change(p,lease)

    def canonical_counts(self):
        return [self.admin('SELECT count(*) FROM companies.'+name)[0][0]
                for name in ('profile_claim','profile_claim_source','g4_section_review')]

    def test_01_default_control_is_disabled_and_roles_are_narrow(self):
        self.admin('UPDATE companies.automation_control SET enabled=false')
        with self.assertRaisesRegex(c.ContractError,'POLICY_DISABLED'):self.port().read_current((CID,))
        with cloud_connect(self.endpoint) as conn:
            for sql in ('SELECT * FROM companies.automation_control','UPDATE companies.company SET website=NULL',
                        'CREATE TABLE companies.forbidden(id int)'):
                with self.subTest(sql=sql),self.assertRaises(Exception) as cm:conn.execute(sql)
                self.assertEqual(cm.exception.sqlstate,'42501')
        roles=self.admin("SELECT rolname,rolsuper,rolbypassrls,rolcreaterole FROM pg_roles WHERE rolname IN ('bohun_companies_owner_v0','bohun_companies_worker_v0')")
        self.assertTrue(all(not any(row[1:]) for row in roles));self.assertEqual(len(roles),2)
        self.admin("ALTER ROLE exp016_other LOGIN PASSWORD "+self.literal(self.state['worker_password']))
        import psycopg
        with psycopg.connect(host='127.0.0.1',port=self.state['port'],dbname='exp016_proof',user='exp016_other',
                             password=self.state['worker_password'],autocommit=True) as conn:
            with self.assertRaises(Exception) as cm:conn.execute('SELECT companies.automation_rpc_v0(%s,%s::jsonb)',('status','{}'))
            self.assertEqual(cm.exception.sqlstate,'42501')

    def test_01b_lock_grant_does_not_let_owner_change_control(self):
        with self.connect_admin() as conn:
            conn.execute('SET ROLE bohun_companies_owner_v0')
            with self.assertRaises(Exception) as cm:
                conn.execute('UPDATE companies.automation_control SET updated_at=clock_timestamp()')
        self.assertEqual(cm.exception.sqlstate,'42501')

    def test_02_missing_trusted_reader_is_not_empty_success(self):
        self.admin('DROP FUNCTION companies.automation_profile_read_v0(text[])')
        with self.assertRaisesRegex(c.ContractError,'PROFILE_READER_NOT_READY'):self.port().read_current((CID,))

    def test_03_python_sql_hash_agreement(self):
        p,lease,ch=self.pending()
        values=[{'україна':'Łabędy','escape':'\n\t"\\','negative':False},w.pack(POLICY)]
        for v in values:
            actual=self.admin('SELECT companies._automation_hash(%s::jsonb)',(c.json_text(v),))[0][0]
            self.assertEqual(actual,c.digest(v))
        self.assertEqual(self.admin('SELECT companies._automation_job_key(%s::jsonb)',(c.json_text(w.encode(lease.job)),))[0][0],lease.job.job_key)
        self.assertEqual(self.admin('SELECT companies._automation_changes_hash(%s::jsonb)',(c.json_text(w.encode(ch)),))[0][0],ch.fingerprint)
        for stamp in ('2026-11-02T00:00:00+00:00','2026-11-02T00:00:00.123456+00:00'):
            raw=self.admin('SELECT companies._automation_time(%s::timestamptz)',(stamp,))[0][0]
            self.assertEqual(w.timestamp(raw),datetime.fromisoformat(stamp))

    def test_04_two_claim_sessions_never_share_a_lease(self):
        other='co_synthetic_second';self.add_company(other);self.enqueue(tuple(sorted((CID,other))))
        barrier=threading.Barrier(2)
        def claim():
            p=self.port();barrier.wait(timeout=5)
            try:return p.claim(POLICY.fingerprint,now(),300)
            except c.ContractError as exc:
                self.assertIn(str(exc),('TX_SERIALIZATION_ROLLBACK','TX_DEADLOCK_ROLLBACK'));return None
        with ThreadPoolExecutor(max_workers=2) as pool:leases=list(pool.map(lambda _:claim(),range(2)))
        leases=[x for x in leases if x is not None]
        # A known rolled-back transaction may be attempted once after the race;
        # never replay an unknown COMMIT or external processor.
        if len(leases)==1:leases.append(self.port().claim(POLICY.fingerprint,now(),300))
        self.assertEqual(len(leases),2);self.assertEqual(len({x.job.job_key for x in leases}),2)

    def test_05_expired_token_is_fenced_after_reclaim(self):
        p,_=self.enqueue();old=p.claim(POLICY.fingerprint,now(),300)
        self.admin("UPDATE companies.automation_job SET lease_until=clock_timestamp()-interval '1 second'")
        new=self.port().claim(POLICY.fingerprint,now(),300)
        self.assertNotEqual(new.token,old.token);self.assertEqual(new.attempt,old.attempt+1)
        with self.assertRaises(Exception):p.defer(old,'FAILED','OLD_WORKER',None,now())
        self.assertEqual(self.admin('SELECT lease_token::text FROM companies.automation_job')[0][0],new.token)

    def test_06_manual_edit_conflicts_with_snapshot_without_jobs(self):
        p=self.port();rev,_,cp=p.read_current((CID,))
        self.admin('UPDATE companies.company SET website=%s WHERE id=%s',('https://manual.example.test',CID))
        j=self.job();prepared={'jobs':(j,),'existing_keys':frozenset([j.job_key]),'seen_triggers':cp['seen_triggers'],
          'section_inputs':cp['section_inputs'],'profile_hashes':cp['profile_hashes'],'unresolved_companies':(),
          'checks':(),'external_calls':0,'persistent_write_performed':False,'assessments':{}}
        self.assertEqual(p.commit_batch(rev,prepared),'CONFLICT')
        self.assertEqual(self.admin('SELECT count(*) FROM companies.automation_job')[0][0],0)

    def test_07_private_writer_and_prepared_restart(self):
        p,lease,ch=self.pending();p.save_prepared(lease,ch,now())
        restarted=self.port();self.assertEqual(restarted.prepared_result(lease),ch)
        self.assertEqual(restarted.commit_result(lease,ch,now()).state,'SUCCEEDED')
        self.assertEqual(self.canonical_counts(),[1,1,1])
        self.assertEqual(self.admin('SELECT qa_status,public_release FROM companies.profile_claim'),[('HOLD',False)])
        self.assertEqual(self.admin('SELECT website FROM companies.company WHERE id=%s',(CID,))[0][0],'https://rotor.example.test')

    def test_08_lost_commit_ack_is_reconciled_once(self):
        p,lease,ch=self.pending();p.save_prepared(lease,ch,now());lost=[False]
        def lose(action):
            if action=='commit_result' and not lost[0]:lost[0]=True;raise TimeoutError()
        rpc=self.transport(after_commit=lose);port=PostgresBackend(rpc,policy=POLICY,clock=now,card_factory=Card)
        from collectors.companies.automation.worker import UnknownCommit
        with self.assertRaises(UnknownCommit):port.commit_result(lease,ch,now())
        receipt=port.lookup_result(lease.job.job_key,ch.fingerprint)
        self.assertEqual(receipt.state,'SUCCEEDED');self.assertEqual(self.canonical_counts(),[1,1,1])

    def test_09_second_invalid_evidence_rolls_back_first_insert(self):
        p,lease,ch=self.pending();h=ch.claims[0];prop=h.proposal
        ev=replace(prop.evidence[0],quote='not a source quote',observed_value_json='"different"')
        second=replace(prop,claim_ref='bad-second',value_json='"different"',evidence=(ev,))
        bad=replace(ch,claims=(h,replace(h,proposal=second)))
        rpc=self.transport();args={'job_key':lease.job.job_key,'token':lease.token,'now':now().isoformat()}
        def raw(action,extra):return rpc(action,{'wire_version':1,'policy_hash':POLICY.fingerprint,'args':args|extra})
        raw('save_prepared',{'changes':w.encode(bad),'result_hash':bad.fingerprint})
        before=self.canonical_counts()
        from collectors.companies.automation.postgres import BackendFault
        with self.assertRaisesRegex(BackendFault,'EVIDENCE_INVALID'):raw('commit_result',{'result_hash':bad.fingerprint})
        self.assertEqual(self.canonical_counts(),before)
        self.assertEqual(self.admin('SELECT state,receipt FROM companies.automation_job'),[('LEASED',None)])

    def test_10_source_admission_rejection_preserves_profile(self):
        p,lease,ch=self.pending();p.save_prepared(lease,ch,now())
        self.admin('UPDATE exp016_fixture.source SET admitted=false')
        with self.assertRaisesRegex(c.ContractError,'SOURCE_ADMISSION_REQUIRED'):p.commit_result(lease,ch,now())
        self.assertEqual(self.canonical_counts(),[0,0,0])

    def test_11_rejected_hash_cannot_be_evaded_by_new_ref(self):
        p,lease,ch=self.pending();prop=ch.claims[0].proposal
        self.admin('INSERT INTO companies.profile_claim(company_id,claim_ref,field_path,claim_value,assessment,content_sha256,qa_status) '
          'VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s)',(CID,'old-rejected',prop.field_path,prop.value_json,'company_claim',prop.content_hash,'REJECTED'))
        p.save_prepared(lease,ch,now())
        with self.assertRaisesRegex(c.ContractError,'REJECTED_CONTENT'):p.commit_result(lease,ch,now())
        self.assertEqual(self.admin('SELECT qa_status FROM companies.profile_claim'),[('REJECTED',)])

    def test_12_manual_owner_denies_hostile_fill(self):
        p,lease,ch=self.pending()
        ctx=p.context(lease);view=replace(ctx.view,fields=(('/identity/website',c.FieldValue(False,owner='manual')),))
        ctx=replace(ctx,view=view)
        self.admin('UPDATE exp016_fixture.profile SET context=%s::jsonb WHERE company_id=%s',(c.json_text(w.encode(ctx)),CID))
        bad=replace(ch,base_fingerprint=view.fingerprint)
        p.save_prepared(lease,bad,now())
        with self.assertRaisesRegex(c.ContractError,'OWNERSHIP_NOT_ADMITTED'):p.commit_result(lease,bad,now())
        self.assertEqual(self.canonical_counts(),[0,0,0])

    def test_13_stop_blocks_next_write(self):
        p,lease,ch=self.pending();p.save_prepared(lease,ch,now())
        self.admin('UPDATE companies.automation_control SET enabled=false')
        with self.assertRaisesRegex(c.ContractError,'POLICY_DISABLED'):p.commit_result(lease,ch,now())
        self.assertEqual(self.canonical_counts(),[0,0,0])

    def test_14_registry_two_complete_sweeps_and_restart(self):
        self.admin('DELETE FROM exp016_fixture.profile; DELETE FROM companies.company')
        # Bulk insert synthetic rows; all actual planning still goes through private RPC.
        self.admin("INSERT INTO companies.company(id,name,name_norm,country) SELECT 'co_synthetic_'||lpad(i::text,5,'0'),"
          "'Synthetic '||i,'synthetic '||i,'DE' FROM generate_series(0,3750) AS i")
        with self.connect_admin() as conn:
            with conn.cursor() as cur:
                for i in range(3751):
                    cid=f'co_synthetic_{i:05}';snapshot=c.CompanySnapshot(cid,c.digest(cid))
                    ctx=WorkContext(c.CompanyView(cid,country='DE'),c.digest(['input',cid]))
                    cur.execute('INSERT INTO exp016_fixture.profile(company_id,card,context,properties_json) VALUES(%s,%s::jsonb,%s::jsonb,%s)',
                      (cid,c.json_text(w.encode(snapshot)),c.json_text(w.encode(ctx)),c.json_text({'Legal name':cid,'Country':'DE'})))
        for cycle in range(2):
            visited=[]
            while True:
                p=self.port();page=p.read_registry_page(limit=100)
                ids=tuple(x.company_id for x in page['companies']);visited.extend(ids)
                self.assertEqual(p.plan_registry_page(page,tuple(self.job(cid) for cid in ids)),'APPLIED')
                if not page['has_more']:break
            self.assertEqual(visited,[f'co_synthetic_{i:05}' for i in range(3751)])
            self.assertEqual(self.admin('SELECT count(*) FROM companies.automation_job')[0][0],3751)

    def test_15_a26_history_survives_registry_and_empty_repeat(self):
        trigger=trigger_receipt(CID,'synthetic-origin',c.digest('document'))
        p,jobs=self.enqueue(trigger=trigger)
        for _ in range(2):
            p=self.port();rev,_,cp=p.read_current((CID,))
            batch={'jobs':(),'existing_keys':cp['existing_keys'],'seen_triggers':cp['seen_triggers'],
              'section_inputs':cp['section_inputs'],'profile_hashes':cp['profile_hashes'],
              'unresolved_companies':(),'checks':(),'external_calls':0,'persistent_write_performed':False,'assessments':{}}
            self.assertEqual(p.commit_batch(rev,batch),'APPLIED')
        p=self.port();page=p.read_registry_page();self.assertEqual(p.plan_registry_page(page,jobs),'APPLIED')
        self.assertEqual(self.admin('SELECT count(*) FROM companies.automation_job')[0][0],1)
        self.assertEqual(self.admin("SELECT count(*) FROM companies.observation WHERE source='exp016.a26_trigger'")[0][0],1)

    def test_16_unknown_owner_does_not_authorize_fill(self):
        p,lease,ch=self.pending();ctx=p.context(lease)
        view=replace(ctx.view,fields=(('/identity/website',c.FieldValue(False,owner='unknown')),))
        self.admin('UPDATE exp016_fixture.profile SET context=%s::jsonb',(c.json_text(w.encode(replace(ctx,view=view))),))
        ch=replace(ch,base_fingerprint=view.fingerprint);p.save_prepared(lease,ch,now())
        with self.assertRaisesRegex(c.ContractError,'OWNERSHIP_NOT_ADMITTED'):p.commit_result(lease,ch,now())
        self.assertEqual(self.canonical_counts(),[0,0,0])

    def test_17_empty_payload_is_not_evidence_of_negative_search(self):
        p,lease,ch=self.pending()
        ch=replace(ch,claims=(),fills=(),issues=(),coverage='RESEARCHED_NOT_FOUND')
        p.save_prepared(lease,ch,now())
        with self.assertRaisesRegex(c.ContractError,'RESEARCH_RECEIPT_REQUIRED'):p.commit_result(lease,ch,now())
        self.assertEqual(self.canonical_counts(),[0,0,0])

    def test_18_approved_coverage_survives_new_hold(self):
        p,lease,ch=self.pending();ctx=p.context(lease)
        view=replace(ctx.view,section_states=(('IDENTITY','HAS_APPROVED_CLAIMS'),))
        self.admin('UPDATE exp016_fixture.profile SET context=%s::jsonb',(c.json_text(w.encode(replace(ctx,view=view))),))
        self.admin("INSERT INTO companies.g4_section_review(company_id,section_key,coverage_state,reviewed_at,qa_status) "
                   "VALUES(%s,'IDENTITY','HAS_APPROVED_CLAIMS',clock_timestamp(),'APPROVED')",(CID,))
        ch=replace(ch,base_fingerprint=view.fingerprint,coverage='HAS_APPROVED_CLAIMS')
        p.save_prepared(lease,ch,now());p.commit_result(lease,ch,now())
        self.assertEqual(self.admin('SELECT coverage_state,qa_status FROM companies.g4_section_review'),[('HAS_APPROVED_CLAIMS','APPROVED')])
        self.assertEqual(self.admin('SELECT qa_status,public_release FROM companies.profile_claim'),[('HOLD',False)])

    def test_19_hostile_cross_company_claim_rejected_by_sql(self):
        p,lease,ch=self.pending();h=ch.claims[0]
        bad=replace(ch,claims=(replace(h,proposal=replace(h.proposal,company_id='co_synthetic_foreign')),))
        rpc=self.transport();args={'job_key':lease.job.job_key,'token':lease.token,'now':now().isoformat()}
        def raw(action,extra):return rpc(action,{'wire_version':1,'policy_hash':POLICY.fingerprint,'args':args|extra})
        raw('save_prepared',{'changes':w.encode(bad),'result_hash':bad.fingerprint})
        from collectors.companies.automation.postgres import BackendFault
        with self.assertRaisesRegex(BackendFault,'CLAIM_SCOPE_INVALID'):raw('commit_result',{'result_hash':bad.fingerprint})
        self.assertEqual(self.canonical_counts(),[0,0,0])

    def test_20_actual_serialization_abort_is_classified_as_rollback(self):
        from .rpc_transport import SQL
        def connect():
            conn=cloud_connect(self.endpoint);outer=self
            class Interleaved:
                def execute(self,sql,args=None):
                    if sql==SQL:
                        conn.execute('SELECT count(*) FROM pg_catalog.pg_class')
                        outer.admin('UPDATE companies.automation_control SET enabled=false')
                    return conn.execute(sql,args)
                def commit(self):return conn.commit()
                def rollback(self):return conn.rollback()
                def close(self):return conn.close()
            return Interleaved()
        from collectors.companies.automation.postgres import BackendFault
        with self.assertRaisesRegex(BackendFault,'TX_SERIALIZATION_ROLLBACK'):
            RpcTransport(connect)('claim',{'wire_version':1,'policy_hash':POLICY.fingerprint,
                                         'args':{'now':now().isoformat(),'lease_seconds':300}})
        self.assertEqual(self.canonical_counts(),[0,0,0])

    def test_21_stop_racing_with_commit_has_no_partial_write(self):
        p,lease,ch=self.pending();p.save_prepared(lease,ch,now())
        with self.connect_admin() as admin:
            admin.execute('BEGIN');admin.execute('SELECT * FROM companies.automation_control FOR UPDATE')
            started=threading.Event()
            def commit():
                def connect():started.set();return cloud_connect(self.endpoint)
                port=PostgresBackend(RpcTransport(connect),policy=POLICY,clock=now,card_factory=Card)
                try:return port.commit_result(lease,ch,now()).state
                except c.ContractError as exc:return str(exc)
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(commit);self.assertTrue(started.wait(5))
                deadline=time.monotonic()+2
                waiting=False
                while time.monotonic()<deadline:
                    waiting=bool(self.admin("SELECT count(*) FROM pg_stat_activity WHERE application_name='exp016_disposable_proof' AND wait_event_type='Lock'")[0][0])
                    if waiting:break
                    time.sleep(.01)
                self.assertTrue(waiting,'no observed database lock barrier')
                admin.execute('UPDATE companies.automation_control SET enabled=false');admin.commit()
                self.assertIn(future.result(timeout=5),('TX_SERIALIZATION_ROLLBACK','POLICY_DISABLED'))
        self.assertEqual(self.canonical_counts(),[0,0,0])

    def test_22_sensitive_ukrainian_geography_is_denied_by_sql(self):
        from .rpc_transport import SQL
        from collectors.companies.automation.changes import ChangeSet,HeldClaim
        self.admin('UPDATE companies.company SET country=%s WHERE id=%s',('UA',CID))
        ctx=WorkContext(c.CompanyView(CID,country='UA'),c.digest(['input',CID]))
        self.admin('UPDATE exp016_fixture.profile SET context=%s::jsonb',(c.json_text(w.encode(ctx)),))
        job=self.job(section='GEOGRAPHY');p=self.port();page=p.read_registry_page()
        self.assertEqual(p.plan_registry_page(page,(job,)),'APPLIED');lease=p.claim(POLICY.fingerprint,now(),300)
        captured=now()-timedelta(days=1);text='Synthetic company says its city is Synthetic City.'
        src=c.SourceDocument('synthetic-geo','https://geo.example.test/about',text,captured,'official_company')
        q=c.Qualifiers(as_of=captured.date().isoformat());value=c.json_text('Synthetic City')
        ev=c.Evidence(src,src.content_hash,CID,'saved_binding',0,len(text),text,q,'/geography/city',value)
        prop=c.ProposedClaim(CID,'GEOGRAPHY','synthetic-geo-claim','/geography/city',value,(ev,),q)
        self.admin('INSERT INTO companies.profile_source(company_id,source_ref,title,source_url,source_kind,as_of) VALUES(%s,%s,%s,%s,%s,%s)',
          (CID,src.source_ref,'Synthetic',src.url,src.kind,captured.date()))
        self.admin('INSERT INTO exp016_fixture.source(company_id,source_ref,url,text_hash,binding) VALUES(%s,%s,%s,%s,%s)',
          (CID,src.source_ref,src.url,src.content_hash,ev.binding))
        ch=ChangeSet(CID,'GEOGRAPHY',ctx.view.fingerprint,POLICY.fingerprint,(HeldClaim(prop,'company_claim'),),(),(),
                     'HAS_HOLD_CLAIMS',now().isoformat())
        p.save_prepared(lease,ch,now())
        with self.assertRaisesRegex(c.ContractError,'SENSITIVE_GEOGRAPHY'):p.commit_result(lease,ch,now())
        self.assertEqual(self.canonical_counts(),[0,0,0])

    def test_23_lease_expiring_while_waiting_for_company_lock_cannot_write(self):
        p,lease,ch=self.pending();p.save_prepared(lease,ch,now())
        self.admin("UPDATE companies.automation_job SET lease_until=clock_timestamp()+interval '1.5 seconds'")
        from collectors.companies.automation.worker import LeaseLost
        def commit():
            try:self.port().commit_result(lease,ch,now());return 'WROTE'
            except LeaseLost:return 'EXPIRED'
        with self.connect_admin() as admin:
            admin.execute('BEGIN');admin.execute('SELECT * FROM companies.company WHERE id=%s FOR UPDATE',(CID,))
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(commit);deadline=time.monotonic()+1;waiting=False
                while time.monotonic()<deadline:
                    waiting=bool(self.admin("SELECT count(*) FROM pg_stat_activity WHERE application_name='exp016_disposable_proof' AND wait_event_type='Lock'")[0][0])
                    if waiting:break
                    time.sleep(.01)
                self.assertTrue(waiting,'no company lock barrier observed')
                time.sleep(1.6)
                admin.commit();self.assertEqual(future.result(timeout=5),'EXPIRED')
        self.assertEqual(self.canonical_counts(),[0,0,0])
