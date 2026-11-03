"""RPC protocol tests. The fake below does not execute or validate candidate SQL."""
from dataclasses import dataclass, replace
from datetime import timedelta
import copy
import unittest

from collectors.companies.automation import contracts as c, checkpoint as w
from collectors.companies.automation.postgres import PostgresBackend, BackendFault, trigger_receipt
from collectors.companies.automation.worker import run_one, UnknownCommit, LeaseLost, ExternalOutcomeUnknown
from companies_automation.test_changes import CID,NOW,POLICY
from companies_automation.test_worker import FakeQueue,success


@dataclass(frozen=True)
class Card:
    company:c.CompanySnapshot
    properties_json:str
    read_at:object
    complete:bool


def batch(jobs=(), *, previous=None, trigger=None):
    cp=copy.deepcopy(previous or w.empty_checkpoint())
    cp['existing_keys']=frozenset(set(cp['existing_keys'])|{j.job_key for j in jobs})
    if trigger:cp['seen_triggers']=frozenset(set(cp['seen_triggers'])|{trigger['key']})
    return {'jobs':tuple(jobs),'existing_keys':cp['existing_keys'],'seen_triggers':cp['seen_triggers'],
            'section_inputs':cp['section_inputs'],'profile_hashes':cp['profile_hashes'],
            'unresolved_companies':(),'checks':(),'external_calls':0,'persistent_write_performed':False,'assessments':{}}


class FakeRPC:
    def __init__(self,queue=None):
        self.queue=queue;self.calls=[];self.last_lease=None
        self.fail_before=set();self.fail_after=set();self.hide=set();self.receipts={}
        self.jobs={};self.seen={};self.inputs={};self.hashes={};self.views={};self.versions={}
        self.reader_complete=True;self.reader_at=NOW;self.manual_complete=True
        self.known_revisions={};self.current_keys=set();self.batch_mutations=0
        self.sweep_after=None;self.sweep_revision=0
        self.add_company(CID)

    def add_company(self,cid):
        self.views[cid]=c.CompanySnapshot(cid,c.digest(['profile',cid]))

    def cp(self,ids):
        cp=w.empty_checkpoint()
        cp['existing_keys']=frozenset(k for k,j in self.jobs.items() if j.company_id in ids)|frozenset(self.current_keys)
        cp['seen_triggers']=frozenset(k for k,t in self.seen.items() if t['company_id'] in ids)
        cp['section_inputs']={k:v for k,v in self.inputs.items() if k[0] in ids}
        cp['profile_hashes']={k:v for k,v in self.hashes.items() if k[0] in ids}
        return cp

    def revision(self,ids):
        return c.digest([list(ids),[w.encode(self.views[i]) for i in ids],w.encode_checkpoint(self.cp(ids))])

    def page_revision(self,ids):
        return c.digest([self.revision(ids),self.sweep_revision])

    def __call__(self,action,request):
        self.calls.append(action)
        if action in self.fail_before:raise TimeoutError('PRIVATE transport error')
        if request['policy_hash']!=POLICY.fingerprint:raise BackendFault('POLICY_CHANGED')
        a=request['args']
        try:result=self.apply(action,a)
        except LeaseLost:raise BackendFault('LEASE_LOST') from None
        except c.ContractError as exc:raise BackendFault(str(exc)) from None
        if action in self.fail_after:
            self.fail_after.remove(action);raise TimeoutError('lost ACK')
        if action in self.hide:result=None
        return {'wire_version':1,'status':'OK','data':copy.deepcopy(result)}

    def apply(self,action,a):
        if action=='read_registry_page':
            c.require(a['after'] is None or a['after']==self.sweep_after,'REGISTRY_CURSOR_CHANGED')
            ids=tuple(i for i in sorted(self.views) if self.sweep_after is None or i>self.sweep_after)
            chosen=ids[:a['limit']]
            return {'companies':[w.encode(self.views[i]) for i in chosen],
                    'checkpoint':chosen[-1] if chosen else self.sweep_after,'after':self.sweep_after,
                    'has_more':len(ids)>len(chosen),'revision':self.page_revision(chosen) if chosen else None,
                    'state':w.encode_checkpoint(self.cp(chosen))}
        if action=='read_current':
            ids=tuple(a['company_ids']);revision=self.revision(ids);self.known_revisions[revision]=ids
            return {'revision':revision,'policy_hash':POLICY.fingerprint,'company_ids':list(ids),
                    'cards':[{'company':w.encode(self.views[i]),'properties_json':c.json_text({'Legal name':i,'Country':'DE'}),
                              'read_at':self.reader_at.isoformat(),'complete':self.reader_complete,
                              'manual_ownership_complete':self.manual_complete} for i in ids],
                    'checkpoint':w.encode_checkpoint(self.cp(ids))}
        if action in ('commit_batch','plan_page'):
            key=a['batch_key'];body={k:v for k,v in a.items() if k!='batch_key'}
            c.require(c.digest(body)==key,'BATCH_KEY_INVALID')
            if key in self.receipts:return self.receipts[key]
            ids=tuple(a['company_ids'])
            if a['revision']!=(self.page_revision(ids) if action=='plan_page' else self.revision(ids)):
                return {'state':'CONFLICT'}
            if action=='plan_page':
                c.require(a['sweep_after']==self.sweep_after,'REGISTRY_CURSOR_CHANGED')
                remaining=tuple(i for i in sorted(self.views) if self.sweep_after is None or i>self.sweep_after)
                c.require(ids==remaining[:len(ids)],'REGISTRY_PAGE_CHANGED')
                c.require(a['sweep_next']==(ids[-1] if len(remaining)>len(ids) else None),'REGISTRY_CURSOR_CHANGED')
            cp=w.decode_checkpoint(a['checkpoint']);old=self.cp(ids)
            c.require(old['existing_keys']<=cp['existing_keys'] and old['seen_triggers']<=cp['seen_triggers'],'HISTORY_LOST')
            jobs=dict(self.jobs);seen=dict(self.seen)
            for raw in a['jobs']:
                j=w.decode(raw,c.JobSpec);c.require(j.company_id in ids,'FOREIGN_COMPANY');jobs.setdefault(j.job_key,j)
            for t in a['triggers']:
                c.require(t['company_id'] in ids,'FOREIGN_TRIGGER');seen.setdefault(t['key'],t)
            c.require(cp['existing_keys']<=set(jobs)|self.current_keys,'UNKNOWN_JOB')
            c.require(cp['seen_triggers']<=set(seen),'UNKNOWN_TRIGGER')
            self.jobs=jobs;self.seen=seen;self.inputs.update(cp['section_inputs']);self.hashes.update(cp['profile_hashes'])
            if self.queue is None and a['jobs']:self.queue=FakeQueue(job=w.decode(a['jobs'][0],c.JobSpec))
            receipt={'state':'APPLIED','batch_key':key};self.receipts[key]=receipt;self.batch_mutations+=1
            if action=='plan_page':self.sweep_after=a['sweep_next'];self.sweep_revision+=1
            return receipt
        if action=='lookup_batch':return self.receipts.get(a['batch_key'])
        q=self.queue
        if action=='claim':
            self.last_lease=q.claim(POLICY.fingerprint,w.timestamp(a['now']),a['lease_seconds']) if q else None
            return w.encode(self.last_lease) if self.last_lease else None
        if action=='lookup_result':
            r=q.lookup_result(a['job_key'],a['result_hash']);return w.encode(r) if r else None
        lease=self.last_lease
        c.require(lease and lease.job.job_key==a['job_key'] and lease.token==a['token'],'LEASE_LOST')
        if action=='context':return w.encode(q.context(lease))
        if action=='read_prepared':return w.encode(q.prepared) if q.prepared else None
        if action=='save_prepared':
            ch=w.decode(a['changes']);q.save_prepared(lease,ch,w.timestamp(a['now']));return {'result_hash':ch.fingerprint}
        if action=='commit_result':
            c.require(q.prepared.fingerprint==a['result_hash'],'RESULT_CHANGED')
            return w.encode(q.commit_result(lease,q.prepared,w.timestamp(a['now'])))
        if action=='defer':
            q.defer(lease,a['state'],a['reason'],w.timestamp(a['not_before']) if a['not_before'] else None,w.timestamp(a['now']))
            return {'state':a['state']}
        raise BackendFault('ACTION_DENIED')


def backend(rpc,**kw):
    return PostgresBackend(rpc,policy=POLICY,clock=lambda:NOW,card_factory=Card,**kw)


class PostgresPortTests(unittest.TestCase):
    def test_lost_claim_ack_stops_without_claim_retry(self):
        rpc=FakeRPC(FakeQueue());rpc.fail_after.add('claim')
        with self.assertRaises(UnknownCommit):backend(rpc).claim(POLICY.fingerprint,NOW,300)
        self.assertEqual(rpc.calls,['claim']);self.assertIsNotNone(rpc.last_lease)

    def test_original_worker_runs_through_rpc_port(self):
        rpc=FakeRPC(FakeQueue());port=backend(rpc)
        result=run_one(port,success,policy=POLICY,clock=lambda:NOW)
        self.assertEqual(result.state,'SUCCEEDED');self.assertEqual(len(rpc.queue.claims),1)
        self.assertEqual(run_one(port,success,policy=POLICY,clock=lambda:NOW).state,'IDLE')

    def test_lost_commit_ack_reads_receipt_without_second_commit(self):
        rpc=FakeRPC(FakeQueue());rpc.fail_after.add('commit_result')
        self.assertEqual(run_one(backend(rpc),success,policy=POLICY,clock=lambda:NOW).state,'SUCCEEDED')
        self.assertEqual(rpc.calls.count('commit_result'),1);self.assertEqual(rpc.calls.count('lookup_result'),1)

    def test_unresolved_commit_no_second_mutation(self):
        rpc=FakeRPC(FakeQueue());rpc.fail_after.add('commit_result');rpc.hide.add('lookup_result')
        self.assertEqual(run_one(backend(rpc),success,policy=POLICY,clock=lambda:NOW).state,'COMMIT_UNKNOWN')
        self.assertEqual(rpc.calls.count('commit_result'),1)

    def test_lost_prepared_ack_reconciles(self):
        rpc=FakeRPC(FakeQueue());rpc.fail_after.add('save_prepared')
        self.assertEqual(run_one(backend(rpc),success,policy=POLICY,clock=lambda:NOW).state,'SUCCEEDED')
        self.assertEqual(rpc.calls.count('save_prepared'),1)

    def test_unresolved_prepared_does_not_commit(self):
        rpc=FakeRPC(FakeQueue());rpc.fail_before.add('save_prepared')
        result=run_one(backend(rpc),success,policy=POLICY,clock=lambda:NOW)
        self.assertEqual(result.state,'NEEDS_REVIEW');self.assertNotIn('commit_result',rpc.calls)

    def test_rpc_bad_envelope_and_policy_mismatch(self):
        with self.assertRaises(UnknownCommit):backend(lambda *_:{'wire_version':1,'status':'OK'}).claim(POLICY.fingerprint,NOW,300)
        rpc=FakeRPC()
        with self.assertRaises(c.ContractError):backend(rpc).claim(c.digest('bad'),NOW,300)
        self.assertEqual(rpc.calls,[])

    def test_read_unavailable_does_not_log_private_exception(self):
        rpc=FakeRPC();rpc.fail_before.add('read_current')
        with self.assertRaises(BackendFault) as cm:backend(rpc).read_current((CID,))
        self.assertNotIn('PRIVATE',str(cm.exception))

    def test_missing_factory_or_incomplete_ownership_is_not_full_profile(self):
        rpc=FakeRPC()
        with self.assertRaisesRegex(c.ContractError,'READER_NOT_READY'):
            PostgresBackend(rpc,policy=POLICY,clock=lambda:NOW).read_current((CID,))
        self.assertEqual(rpc.calls,[])
        rpc.manual_complete=False
        with self.assertRaisesRegex(c.ContractError,'PARTIAL_CARD_READ'):backend(rpc).read_current((CID,))

    def test_partial_stale_and_future_card_reads_denied(self):
        for complete,time in ((False,NOW),(True,NOW-timedelta(minutes=6)),(True,NOW+timedelta(seconds=1))):
            rpc=FakeRPC();rpc.reader_complete=complete;rpc.reader_at=time
            with self.subTest(complete=complete,time=time),self.assertRaises(c.ContractError):backend(rpc).read_current((CID,))

    def test_invalid_company_sets_denied_before_rpc(self):
        rpc=FakeRPC();p=backend(rpc)
        for ids in ((),(CID,CID),tuple(str(i) for i in range(11))):
            with self.subTest(ids=ids),self.assertRaises(c.ContractError):p.read_current(ids)
        self.assertEqual(rpc.calls,[])

    def test_foreign_job_and_missing_trigger_receipt_denied(self):
        rpc=FakeRPC();p=backend(rpc);rev,_,cp=p.read_current((CID,))
        foreign=c.JobSpec('ENRICH_SECTION',POLICY.fingerprint,POLICY.version,c.digest('i'),'foreign','EVENTS')
        with self.assertRaises(c.ContractError):p.commit_batch(rev,batch((foreign,),previous=cp))
        b=batch(previous=cp);b['seen_triggers']=frozenset([c.digest('unknown')])
        with self.assertRaises(c.ContractError):p.commit_batch(rev,b)
        self.assertNotIn('commit_batch',rpc.calls)

    def test_durable_snapshot_history_cannot_be_erased_by_mutating_returned_copy(self):
        rpc=FakeRPC();rpc.current_keys.add(c.digest('old'));p=backend(rpc)
        rev,_,cp=p.read_current((CID,));cp['existing_keys']=frozenset()
        with self.assertRaisesRegex(c.ContractError,'HISTORY_LOST'):p.commit_batch(rev,batch(previous=cp))

    def test_safe_fault_lease_lost_and_unknown_write(self):
        def denied(*_):raise BackendFault('LEASE_LOST')
        with self.assertRaises(LeaseLost):backend(denied).claim(POLICY.fingerprint,NOW,300)
        q=FakeQueue();rpc=FakeRPC(q);p=backend(rpc);lease=p.claim(POLICY.fingerprint,NOW,300)
        rpc.fail_before.add('defer')
        with self.assertRaises(UnknownCommit):p.defer(lease,'FAILED','SAFE_CODE',None,NOW)

    def test_invalid_defer_has_no_remote_call(self):
        rpc=FakeRPC(FakeQueue());p=backend(rpc);lease=p.claim(POLICY.fingerprint,NOW,300);before=len(rpc.calls)
        with self.assertRaises(c.ContractError):p.defer(lease,'WAIT_QUOTA','QUOTA',None,NOW)
        with self.assertRaises(c.ContractError):p.defer(lease,'FAILED','private raw reason',None,NOW)
        self.assertEqual(before,len(rpc.calls))
