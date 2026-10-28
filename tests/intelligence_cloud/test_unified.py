"""Canonical wiring with actual adapters/fake IO. Not PostgreSQL/live proof."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import socket
from unittest import TestCase
from unittest.mock import Mock,patch

from collectors.intelligence_pipeline.cloud import CONTRACT
from collectors.intelligence_pipeline.cloud.runtime import collect_sources,run,live_run,validate_config
from collectors.intelligence_pipeline.cloud.postgres import PostgresStore,PostgresQuota
from collectors.intelligence_pipeline.cloud.notion import NotionDelivery
from collectors.intelligence_pipeline.cloud.http import Reply,CloudError
from collectors.intelligence_pipeline.contracts import Document,digest
from collectors.intelligence_pipeline.source_adapters import admit_source,envelope,web_source,restore_source,source_profile
from collectors.intelligence_pipeline.ports import QuotaExceeded
from collectors.intelligence_pipeline.gemini import ModelAccessStop
import test_cloud
from test_cloud import RPCServer,NotionHTTP,ModelHTTP,profile,approval,model,outputs,provider,NOW,PARENT


def config():
    return {'version':CONTRACT,'approval_ref':'SYNTHETIC G3','expected_sha':'a'*40,'run_scope':'trial',
        'enabled_actions':['capture','model','notion'],'free_approval':approval().__dict__,'notion_parent':PARENT,'max_ticks':15,
        'sources':[{'kind':'inoreader','batch_id':PARENT,'policy_ref':'fixture-inoreader','fetch_new':False}]}


def web_item(text='The notice announced planned output of 20 units.',html=None):
    html=html if html is not None else '<article><p>'+text+'</p></article>'
    captured={'url':'https://example.org/synthetic','received_at':NOW.isoformat(),'html_sha256':hashlib.sha256(html.encode()).hexdigest(),
              'text':text,'text_sha256':hashlib.sha256(text.encode()).hexdigest(),'extractor':'trafilatura-2.3.1'}
    s=web_source(captured,source_id='web:fixture',title='Example public notice',published_at='2026-10-19T10:00:00Z')
    s,a=admit_source(s,profile('web'),html,kind='web',provenance={'capture_hash':digest(captured),'robots':'ALLOWED'})
    return {'value':envelope(s,kind='web',source_db_id=3,assessment_ref='fixture-web'),'raw_html':html,'admission':a}


class RoutedHTTP:
    def __init__(self):self.calls=[];self.context_seen=False;self.bad_quote=False;self.bad_dossier=False;self.fail=None
    def request(self,method,path,headers,body):
        req=json.loads(body['contents'][0]['parts'][0]['text']);stage=req['stage'];data=req['data']
        self.calls.append(stage);self.context_seen|=bool(data.get('context_manifest'))
        if self.fail:raise self.fail
        doc=Document(**{**data['document'],'verified_entity_ids':tuple(data['document']['verified_entity_ids'])})
        out=outputs(doc)[{'extract':0,'analyze':1,'review':2}[stage]]
        if stage=='extract' and self.bad_quote:out['claims'][0]['quote']='invented quote'
        if stage=='analyze' and self.bad_dossier:out['dossier']['hypotheses'][0]['probability']=0.99
        if stage=='review':out['draft_hash']=req['review_hash']
        return Reply(200,provider(out))


class UnifiedTests(TestCase):
    def setUp(self):
        guard=patch.object(socket.socket,'connect',side_effect=AssertionError('OFFLINE_ONLY'))
        guard.start();self.addCleanup(guard.stop)
    def rig(self):
        rpc=RPCServer();http=RoutedHTTP();nh=NotionHTTP()
        return rpc,http,nh,model('key',http),NotionDelivery('token',PARENT,nh)
    def test_two_source_branches_context_dossier_and_restart_without_new_posts(self):
        rpc,http,nh,m,n=self.rig();rpc.intake_value,_=test_cloud.IntakeTests().prepared()
        feed=collect_sources(config(),rpc)
        text='Example Works';rpc.context_records=[{'id':'company:fixture','kind':'company_lead','status':'lead',
            'source_url':'https://example.org/','available_at':'2026-10-18T00:00:00Z','text':text,
            'text_hash':hashlib.sha256(text.encode()).hexdigest(),'quote':text,'root_id':'company:fixture'}]
        sources=feed+[web_item()]
        result=run(rpc,m,n,'trial',sources=sources,clock=lambda:NOW)
        self.assertEqual(result['submitted'],2);self.assertEqual(result['delivery'],['DELIVERED','DELIVERED'])
        self.assertEqual(len(http.calls),6);self.assertTrue(http.context_seen)
        for row in rpc.outbox.values():
            p=row['packet'];self.assertEqual(p['context_manifest'][0]['status'],'lead')
            self.assertEqual(p['qa'],{'decision':'HOLD','public_release':False})
            self.assertTrue(p['dossier']['hypotheses'][0]['refutation'])
        repeated=run(rpc,model('key',http),NotionDelivery('token',PARENT,nh),'trial',sources=sources,clock=lambda:NOW+timedelta(minutes=1))
        self.assertEqual(repeated['calls'],6);self.assertEqual(len(http.calls),6)
        self.assertEqual(sum(c[0]=='POST' for c in nh.calls),2)
        self.assertEqual(len(rpc.store._jobs),6)
    def test_policy_accepts_new_hash_but_truncation_and_sensitive_marker_hold_before_model(self):
        rpc,http,nh,m,n=self.rig()
        one=web_item();two=web_item('The notice announced planned output of 21 units.')
        self.assertTrue(one['admission']['accepted'] and two['admission']['accepted'])
        self.assertNotEqual(one['admission']['text_hash'],two['admission']['text_hash'])
        for item in (web_item(html='<article>truncated'),web_item(text='A sensitive facility is described here.')):
            self.assertFalse(item['admission']['accepted'])
            result=run(rpc,m,n,'trial',sources=[item],clock=lambda:NOW)
            self.assertTrue(result['held'])
        self.assertEqual(http.calls,[]);self.assertEqual(nh.calls,[])
    def test_forged_complete_flag_cannot_override_the_machine_receipt(self):
        rpc,http,nh,m,n=self.rig();item=web_item(html='<article>truncated')
        item['value']['source'].update(completeness='COMPLETE',rights='PERMITTED',sensitive=False)
        with self.assertRaises(ValueError):run(rpc,m,n,'trial',sources=[item],clock=lambda:NOW)
        self.assertFalse(http.calls)
    def test_context_future_quote_and_oversize_rejected_before_generation(self):
        rpc,http,nh,m,n=self.rig();text='Example Works'
        valid={'id':'company:fixture','kind':'company_lead','status':'lead','source_url':'https://example.org/',
            'available_at':'2026-10-18T00:00:00Z','text':text,'text_hash':hashlib.sha256(text.encode()).hexdigest(),'quote':text,'root_id':'r'}
        for change in ({'available_at':'2027-01-01T00:00:00Z'},{'quote':'not literal'},{'text':'x'*32769}):
            rpc.context_records=[valid|change]
            with self.assertRaises(ValueError):run(rpc,m,n,'trial',sources=[web_item()],clock=lambda:NOW)
        self.assertFalse(http.calls)
    def test_bad_dossier_or_quote_is_held_before_delivery(self):
        for fault,expected in (('bad_quote',['extract']),('bad_dossier',['extract','analyze'])):
            rpc,http,nh,m,n=self.rig();setattr(http,fault,True)
            r=run(rpc,m,n,'trial',sources=[web_item()],clock=lambda:NOW)
            self.assertIn('HOLD',r['steps']);self.assertEqual(http.calls,expected);self.assertFalse(nh.calls)
    def test_model_access_stop_or_timeout_keeps_reservation_and_never_retries(self):
        for fault in (ModelAccessStop('FREE_LIMIT'),TimeoutError('PRIVATE')):
            rpc,http,nh,m,n=self.rig();http.fail=fault
            r=run(rpc,m,n,'trial',sources=[web_item()],clock=lambda:NOW)
            self.assertEqual(http.calls,['extract']);self.assertEqual(r['steps'],['HOLD'])
            self.assertEqual(next(iter(rpc.usage.values()))['state'],'UNKNOWN');self.assertFalse(nh.calls)
    def test_missing_expired_or_wrong_key_free_proof_stops_before_any_rpc(self):
        rpc,http,nh,m,n=self.rig()
        for bad in (None,approval('other'),approval().__class__('trial','expired',approval().key_sha256,'2026-01-01T00:00:00Z')):
            m.approval=bad
            with self.assertRaises((ModelAccessStop,ValueError)):run(rpc,m,n,'trial',sources=[web_item()],clock=lambda:NOW)
        self.assertEqual(rpc.calls,[]);self.assertFalse(http.calls)
    def test_shared_quota_stop_is_final_without_tomorrow_retry(self):
        rpc,http,nh,m,n=self.rig()
        for i in range(3):rpc.usage[str(i)]={'charged':329573,'state':'UNKNOWN'}
        result=run(rpc,m,n,'trial',sources=[web_item()],clock=lambda:NOW)
        self.assertEqual(result['steps'],['HOLD']);self.assertEqual(http.calls,[]);self.assertEqual(nh.calls,[])
    def test_notion_dossier_edit_conflicts_and_missing_ack_recovery_does_not_post_again(self):
        rpc,http,nh,m,n=self.rig();nh.fault='timeout_after_commit'
        result=run(rpc,m,n,'trial',sources=[web_item()],clock=lambda:NOW)
        self.assertEqual(result['delivery'],['UNKNOWN'])
        run(rpc,m,n,'trial',sources=[web_item()],clock=lambda:NOW)
        self.assertEqual(sum(c[0]=='POST' for c in nh.calls),1)
        from collectors.intelligence_pipeline.cloud.runtime import reconcile
        key=next(iter(rpc.outbox));self.assertEqual(reconcile(rpc,n,'trial',key)['delivery'],'DELIVERED')
        page=next(iter(nh.pages.values()));i=next(i for i,b in enumerate(page['children']) if 'Досье' in b['paragraph']['rich_text'][0]['text']['content'])
        page['children'][i]['paragraph']['rich_text'][0]['text']['content']='HUMAN DOSSIER'
        self.assertEqual(n.deliver(rpc.outbox[key]['packet'],reconcile_only=True).status,'CONFLICT')
    def test_web_collection_uses_existing_robots_capture_and_exact_policy_boundary(self):
        rpc=RPCServer();c=config();c['sources']=[{'kind':'web','url':'https://example.org/synthetic','policy_ref':'fixture-web',
            'title':'Example public notice','published_at':'2026-10-19T10:00:00Z'}]
        from collectors.web_monitor.content import capture as capture_real
        text='The notice announced planned output of 20 units.'
        def capture_fixture(url,policy,**kw):
            return capture_real(url,policy,fetch=kw['fetch'],extractor=lambda html:text,clock=lambda:NOW)
        fetch=Mock(side_effect=['User-agent: *\nAllow: /','<article><p>'+text+'</p></article>'])
        with patch('collectors.web_monitor.content.download',fetch),patch('collectors.web_monitor.content.capture',capture_fixture):
            result=collect_sources(c,rpc)
        self.assertTrue(result[0]['admission']['accepted']);self.assertEqual(fetch.call_count,2)
        c['sources'][0]['url']='https://outside.example/synthetic';fetch.reset_mock()
        with patch('collectors.web_monitor.content.download',fetch):
            with self.assertRaises(ValueError):collect_sources(c,rpc)
        fetch.assert_not_called()
