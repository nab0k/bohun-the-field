from dataclasses import replace
import unittest

from collectors.companies.automation import contracts as c
from collectors.companies.automation.postgres import trigger_receipt
from collectors.companies.automation.worker import run_one
from companies_automation.test_changes import CID,NOW,POLICY
from companies_automation.test_worker import success
from companies_automation.test_postgres import FakeRPC,backend,batch


def job(cid=CID):
    return c.JobSpec('ENRICH_SECTION',POLICY.fingerprint,POLICY.version,c.digest('input'),cid,'IDENTITY')


class SharedBackendScenarios(unittest.TestCase):
    def test_all_3751_synthetic_companies_are_paged_without_duplicate_jobs(self):
        rpc=FakeRPC();rpc.views={}
        for i in range(3751):rpc.add_company(f'co_synthetic_{i:05}')
        for cycle in range(2):
            visited=[]
            while True:
                p=backend(rpc);page=p.read_registry_page(limit=100)
                ids=tuple(v.company_id for v in page['companies']);visited.extend(ids)
                self.assertEqual(p.plan_registry_page(page,tuple(job(cid) for cid in ids)),'APPLIED')
                if not page['has_more']:break
            self.assertEqual(visited,sorted(rpc.views))
            self.assertEqual(len(rpc.jobs),3751);self.assertIsNone(rpc.sweep_after)

    def test_registry_restart_resumes_and_completed_sweeps_repeat(self):
        rpc=FakeRPC();rpc.add_company('co_synthetic_zulu')
        for cycle in range(3):
            first=backend(rpc);page=first.read_registry_page(limit=1)
            self.assertIsNone(page['after']);self.assertTrue(page['has_more'])
            self.assertEqual(first.plan_registry_page(page,(job(page['companies'][0].company_id),)),'APPLIED')
            resumed=backend(rpc);nextpage=resumed.read_registry_page(limit=1)
            self.assertEqual(nextpage['after'],page['checkpoint']);self.assertFalse(nextpage['has_more'])
            self.assertEqual(resumed.plan_registry_page(nextpage,(job(nextpage['companies'][0].company_id),)),'APPLIED')
            self.assertIsNone(rpc.sweep_after)
        self.assertEqual(len(rpc.jobs),2);self.assertEqual(rpc.sweep_revision,6)

    def test_registry_unknown_ack_advances_once_and_reconciles(self):
        rpc=FakeRPC();rpc.add_company('co_synthetic_zulu');p=backend(rpc)
        page=p.read_registry_page(limit=1);rpc.fail_after.add('plan_page')
        self.assertEqual(p.plan_registry_page(page,(job(),)),'APPLIED')
        self.assertEqual(rpc.calls.count('plan_page'),1)
        self.assertEqual((rpc.sweep_after,rpc.sweep_revision),(CID,1))
        self.assertEqual(backend(rpc).reconcile_batch(p.last_batch),'APPLIED')

    def test_registry_mutated_page_and_unread_page_are_denied(self):
        rpc=FakeRPC();p=backend(rpc);page=p.read_registry_page()
        page['state']['section_inputs'][(CID,'IDENTITY')]=c.digest('fabricated')
        with self.assertRaises(c.ContractError):p.plan_registry_page(page,(job(),))
        with self.assertRaises(c.ContractError):backend(rpc).plan_registry_page(page,(job(),))
        self.assertNotIn('plan_page',rpc.calls);self.assertEqual(rpc.sweep_revision,0)

    def test_registry_conflict_keeps_cursor_and_empty_registry_is_read_only(self):
        rpc=FakeRPC();p=backend(rpc);page=p.read_registry_page()
        rpc.views[CID]=replace(rpc.views[CID],version=c.digest('concurrent edit'))
        self.assertEqual(p.plan_registry_page(page,(job(),)),'CONFLICT')
        self.assertIsNone(rpc.sweep_after);self.assertEqual(rpc.jobs,{})
        rpc.views={};page=p.read_registry_page()
        self.assertEqual(page['companies'],());self.assertFalse(page['has_more'])
        with self.assertRaises(c.ContractError):p.plan_registry_page(page,())

    def test_registry_cursor_and_limits_fail_closed(self):
        rpc=FakeRPC();p=backend(rpc)
        for limit in (0,101,True):
            with self.assertRaises(c.ContractError):p.read_registry_page(limit=limit)
        self.assertEqual(rpc.calls,[])
        with self.assertRaises(c.ContractError):p.read_registry_page(after='unknown')

    def test_two_clients_empty_repeats_preserve_keys(self):
        rpc=FakeRPC();first=backend(rpc);rev,_,cp=first.read_current((CID,))
        self.assertEqual(first.commit_batch(rev,batch((job(),),previous=cp)),'APPLIED')
        for _ in range(3):
            client=backend(rpc);rev,_,cp=client.read_current((CID,))
            self.assertEqual(client.commit_batch(rev,batch(previous=cp)),'APPLIED')
        self.assertEqual(len(rpc.jobs),1);self.assertIn(job().job_key,rpc.jobs)

    def test_other_company_jobs_survive_scoped_batch(self):
        other='co_synthetic_other';rpc=FakeRPC();rpc.add_company(other)
        for cid in (other,CID):
            p=backend(rpc);rev,_,cp=p.read_current((cid,))
            self.assertEqual(p.commit_batch(rev,batch((job(cid),),previous=cp)),'APPLIED')
        self.assertEqual(set(rpc.jobs),{job(CID).job_key,job(other).job_key})

    def test_concurrent_manual_change_causes_zero_write_conflict(self):
        rpc=FakeRPC();p=backend(rpc);rev,_,cp=p.read_current((CID,))
        rpc.views[CID]=replace(rpc.views[CID],version=c.digest('manual edit'))
        self.assertEqual(p.commit_batch(rev,batch((job(),),previous=cp)),'CONFLICT')
        self.assertEqual(rpc.jobs,{})

    def test_two_planners_same_revision_second_conflicts_without_duplicate(self):
        rpc=FakeRPC();a=backend(rpc);b=backend(rpc)
        ra,_,ca=a.read_current((CID,));rb,_,cb=b.read_current((CID,))
        self.assertEqual(a.commit_batch(ra,batch((job(),),previous=ca)),'APPLIED')
        # Exact immutable batch replay may return its receipt, not a second insertion.
        self.assertEqual(b.commit_batch(rb,batch((job(),),previous=cb)),'APPLIED')
        self.assertEqual(len(rpc.jobs),1);self.assertEqual(rpc.batch_mutations,1)

    def test_unknown_batch_ack_reconciled_and_unresolved_is_unknown(self):
        for hide in (False,True):
            rpc=FakeRPC();p=backend(rpc);rev,_,cp=p.read_current((CID,));rpc.fail_after.add('commit_batch')
            if hide:rpc.hide.add('lookup_batch')
            self.assertEqual(p.commit_batch(rev,batch((job(),),previous=cp)),'UNKNOWN' if hide else 'APPLIED')
            self.assertEqual(rpc.calls.count('commit_batch'),1);self.assertEqual(len(rpc.jobs),1)

    def test_saved_receipt_can_be_read_by_new_client_after_restart(self):
        rpc=FakeRPC();p=backend(rpc);rev,_,cp=p.read_current((CID,));p.commit_batch(rev,batch((job(),),previous=cp))
        self.assertEqual(backend(rpc).reconcile_batch(p.last_batch),'APPLIED')

    def test_a26_trigger_and_full_registry_use_one_job_key(self):
        t=trigger_receipt(CID,'synthetic-origin',c.digest('document'),('EVENTS','EVENTS'))
        rpc=FakeRPC();p=backend(rpc,triggers=(t,));rev,_,cp=p.read_current((CID,))
        self.assertEqual(p.commit_batch(rev,batch((job(),),previous=cp,trigger=t)),'APPLIED')
        # Registry planning of the same immutable input adds no second key.
        second=backend(rpc);rev,_,cp=second.read_current((CID,))
        self.assertEqual(second.commit_batch(rev,batch((job(),),previous=cp)),'APPLIED')
        self.assertEqual((len(rpc.jobs),len(rpc.seen)),(1,1))
        self.assertEqual(run_one(backend(rpc),success,policy=POLICY,clock=lambda:NOW).state,'SUCCEEDED')
        self.assertEqual(len(rpc.queue.claims),1)

    def test_one_admitted_trigger_cannot_be_marked_for_other_company(self):
        t=trigger_receipt('other','origin',c.digest('x'))
        rpc=FakeRPC();p=backend(rpc,triggers=(t,));rev,_,cp=p.read_current((CID,))
        with self.assertRaises(c.ContractError):p.commit_batch(rev,batch(previous=cp,trigger=t))
        self.assertEqual((len(rpc.seen),len(rpc.jobs)),(0,0))
