"""Whole local wiring with fake durable state/HTTP, not a live or SQL acceptance."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest import TestCase
from unittest.mock import Mock,patch

from collectors.intelligence_pipeline.contracts import JobSpec,Usage,json_text
from collectors.intelligence_pipeline.gemini import GeminiModel,FreeApproval,ModelAccessStop
from collectors.intelligence_pipeline.notion import NotionDelivery
from collectors.intelligence_pipeline.editorial import MemoryEditorial
from collectors.intelligence_pipeline.quota import MemoryQuota,Limits
from collectors.intelligence_pipeline.runtime import Coordinator,live_run,validate_config
from collectors.intelligence_pipeline.testing import MemoryStore
from test_adapters import NOW,PARENT,NotionHTTP,Outbox,dossier,source_value
from test_pipeline import responses,document


class IntegrationStore(MemoryStore,Outbox):
    def __init__(self):
        MemoryStore.__init__(self);Outbox.__init__(self)
        self.sources=[];self.editorial=MemoryEditorial();self.proposals={}
    def receive(self,value): self.sources.append(value)
    def context(self,doc,*,as_of,limit):
        text='Example Works'
        return [{'id':'company:fixture','kind':'company_lead','status':'lead','source_url':'https://example.org/',
                 'available_at':'2026-10-18T00:00:00Z','text':text,'text_hash':hashlib.sha256(text.encode()).hexdigest(),
                 'quote':text,'root_id':'company:fixture'}]
    def submit_with_context(self,doc,version,context,*,category):
        payload={'document':doc.to_dict(),'requires_dossier':True,'research_category':category,**context}
        return self._enqueue(JobSpec(doc.identity,'extract',json_text(payload),version))
    def ready(self,limit):
        return [json.loads(r['result'].output_json) for r in self._jobs.values()
                if r['result'] and r['result'].status=='EDITORIAL_READY'][:limit]
    def propose(self,packet):
        if packet['material_id'] not in self.proposals:
            v=self.editorial.propose(packet['material_id'],packet['draft']['title'],packet['draft']['body'],
                                     [s['text_hash'] for s in packet['sources']])
            self.proposals[packet['material_id']]={'packet':packet,'material':asdict(v),'content_hash':v.content_hash}
        return self.proposals[packet['material_id']]


class ModelHTTP:
    def __init__(self):self.calls=[];self.context_seen=False;self.bad_dossier=False
    def __call__(self,method,url,headers,body):
        request=json.loads(body['contents'][0]['parts'][0]['text']);stage=request['stage'];p=request['data']
        self.calls.append(stage); self.context_seen|=bool(p.get('context_manifest'))
        d=document();values=responses(d)
        if stage=='extract':out=values[d.identity+':extract']['payload']
        elif stage=='analyze':
            ds=dossier();ds['hypotheses'][0]['context_refs']=['company:fixture']
            if self.bad_dossier:ds['hypotheses'][0]['probability']=0.87
            out={'draft':values[d.identity+':analyze']['payload'],'dossier':ds}
        else:out={'draft_hash':request['review_hash'],'issues':[],'comment':'Needs independent human QA.'}
        return {'modelVersion':'gemini-3.5-flash-lite','serviceTier':'standard',
                'usageMetadata':{'promptTokenCount':100,'candidatesTokenCount':200,'thoughtsTokenCount':10,'totalTokenCount':310},
                'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(out)}]}}]}


class RuntimeIntegrationTests(TestCase):
    def rig(self):
        store=IntegrationStore();send=ModelHTTP();key='fixture-key'
        approval=FreeApproval('fixture','local-test',hashlib.sha256(key.encode()).hexdigest(),'2026-10-20T00:00:00Z')
        model=GeminiModel(key,approval,enabled=True,transport=send,clock=lambda:NOW)
        quota=MemoryQuota('fixture',Limits(15,Usage(16000000,90000,15)))
        notion=NotionHTTP();delivery=NotionDelivery(store,'fake',PARENT,enabled=True,transport=notion)
        runner=Coordinator(store,model,quota,delivery,clock=lambda:NOW)
        return runner,store,send,notion,quota
    def test_source_context_model_dossier_text_and_notion_readback_with_no_duplicate(self):
        runner,store,send,notion,quota=self.rig()
        result=runner.run([source_value()],model_enabled=True,notion_enabled=True)
        self.assertEqual(send.calls,['extract','analyze','review']);self.assertTrue(send.context_seen)
        self.assertEqual(result['deliveries'][0]['state'],'CONFIRMED');self.assertFalse(result['public_release'])
        packet=store.ready(5)[0]
        self.assertEqual(packet['context_status'],'RETRIEVED')
        self.assertEqual(packet['context_manifest'][0]['status'],'lead')
        self.assertEqual(packet['qa'],{'decision':'HOLD','public_release':False})
        self.assertTrue(packet['dossier']['hypotheses'][0]['refutation'])
        self.assertIn('Досье',json.dumps(notion.body,ensure_ascii=False))
        runner.run([source_value()],model_enabled=True,notion_enabled=True)
        self.assertEqual(len(send.calls),3);self.assertEqual([m for m,u in notion.calls].count('POST'),1)
    def test_unknown_rights_completeness_never_reserve_call_or_deliver(self):
        runner,store,send,notion,quota=self.rig()
        result=runner.run([source_value(False)],model_enabled=True,notion_enabled=True)
        self.assertTrue(result['held']);self.assertEqual(send.calls,[]);self.assertEqual(notion.calls,[])
        self.assertEqual(quota.snapshot(NOW)['requests'],0)
    def test_free_quota_stop_halts_same_worker_without_trying_another_document(self):
        runner,store,send,notion,quota=self.rig()
        runner.model.transport=Mock(side_effect=ModelAccessStop('FREE_LIMIT'))
        result=runner.run([source_value()],model_enabled=True)
        self.assertEqual(result['steps'],['HOLD']);runner.model.transport.assert_called_once()
        self.assertEqual(quota.snapshot(NOW)['uncertain_requests'],1)
    def test_exhausted_run_quota_holds_without_call_or_next_day_retry(self):
        runner,store,send,notion,quota=self.rig()
        runner.quota=MemoryQuota('fixture',Limits(1,Usage(1,1,1)))
        result=runner.run([source_value()],model_enabled=True,notion_enabled=True)
        self.assertEqual(result['steps'],['HOLD'])
        self.assertEqual(send.calls,[]);self.assertEqual(notion.calls,[])
        self.assertTrue(all(row['result'] is not None for row in store._jobs.values()))
    def test_unsupported_hypothesis_shape_is_held_before_review_or_delivery(self):
        runner,store,send,notion,quota=self.rig();send.bad_dossier=True
        result=runner.run([source_value()],model_enabled=True,notion_enabled=True)
        self.assertEqual(send.calls,['extract','analyze']);self.assertIn('HOLD',result['steps'])
        self.assertEqual(notion.calls,[])
    def test_network_error_retains_reservation_and_does_not_retry(self):
        runner,store,send,notion,quota=self.rig()
        runner.model.transport=Mock(side_effect=TimeoutError('private'))
        runner.run([source_value()],model_enabled=True)
        self.assertEqual(runner.model.transport.call_count,1)
        self.assertEqual(quota.snapshot(NOW)['uncertain_requests'],1)
    def test_no_live_invocation_never_checks_git_or_connects(self):
        with patch('collectors.intelligence_pipeline.runtime.validate_config',return_value={'expected_sha':'a'}), \
             patch('collectors.inoreader.probe.verify_revision') as revision:
            with self.assertRaises(ValueError): live_run({},allow_live=False,expected_sha='a')
        revision.assert_not_called()
