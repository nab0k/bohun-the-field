"""Selected INPUT29 wiring, fake shared stores/HTTP only. No SQL/live acceptance."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from threading import Event, Lock
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5
import hashlib
import json
from unittest import TestCase
from unittest.mock import Mock, patch

from collectors.intelligence_pipeline.contracts import digest
from collectors.intelligence_pipeline.gemini import GeminiModel, http_json
from collectors.intelligence_pipeline.notion import NotionDelivery, DeliveryUnknown, DeliveryConflict
from collectors.intelligence_pipeline.postgres import PostgresQuota
from collectors.intelligence_pipeline.runtime import Coordinator, RUNTIME_VERSION, validate_config, live_run
from collectors.intelligence_pipeline.source_adapters import restore_source, envelope
from collectors.intelligence_pipeline.ports import VersionConflict
from test_adapters import NOW, PARENT, NotionHTTP, Outbox, packet_record, source_value
from test_costing import BudgetAPI, KEY, approval
from test_runtime import IntegrationStore, ModelHTTP


class LargeBodyHTTP(ModelHTTP):
    """Small literal claim from a large retained document, not a large model answer."""
    def __call__(self, method, url, headers, body):
        reply=super().__call__(method,url,headers,body)
        request=json.loads(body['contents'][0]['parts'][0]['text'])
        if request['stage']=='extract':
            part=reply['candidates'][0]['content']['parts'][0]
            value=json.loads(part['text'])
            value['document_hash']=hashlib.sha256(request['data']['document']['text'].encode()).hexdigest()
            part['text']=json.dumps(value)
        return reply


class VersionedStore(IntegrationStore):
    def __init__(self):
        super().__init__();self.deliveries={};self.delivery_lock=Lock()
    def delivery(self, mid, version):
        return dict(self.deliveries.get((mid,version), {'state':'PENDING','page_id':None}))
    def claim_delivery(self, mid, version, *, expected_hash, packet_hash):
        with self.delivery_lock:
            record=self.proposals[mid]
            if record['content_hash']!=expected_hash or digest(record['packet'])!=packet_hash:
                raise VersionConflict('HASH_CHANGED')
            old=self.delivery(mid,version)
            if old['state']!='PENDING':return dict(old,claimed=False,token=None)
            value={'state':'SENDING','page_id':None,'token':str(uuid5(NAMESPACE_URL,mid+str(version)))}
            self.deliveries[mid,version]=value
            return dict(value,claimed=True)
    def mark_delivery(self, mid, version, state, *, page_id=None, expected_hash, token=None):
        with self.delivery_lock:
            old=self.deliveries[mid,version]
            if old['state']=='SENDING' and token!=old['token']:raise VersionConflict('STALE')
            if old['state'] not in {'SENDING','UNKNOWN'}:raise VersionConflict('STATE')
            self.deliveries[mid,version]={'state':state,'page_id':page_id}


class Pages:
    def __init__(self):self.pages={};self.calls=[]
    def __call__(self, method, url, headers, body=None):
        self.calls.append((method,url));path=urlsplit(url).path
        if method=='POST':
            title=body['properties']['title']['title'][0]['text']['content']
            identifier=str(uuid5(NAMESPACE_URL,title));self.pages[identifier]=deepcopy(body)
            return {'id':identifier}
        if '/pages/' in path:
            value=self.pages[path.rsplit('/',1)[1]]
            return {'properties':value['properties'],'parent':value['parent']}
        identifier=path.split('/')[3]
        blocks=deepcopy(self.pages[identifier]['children'])
        for block in blocks:block['has_children']=False
        return {'has_more':False,'results':blocks}


def config():
    return dict(version=RUNTIME_VERSION, approval_ref='SYNTHETIC-LOCAL-NOT-LIVE', expected_sha='a'*40,
                run_scope='one-reviewed-run',enabled_actions=['capture','model','notion'],
                model_access=asdict(approval()),notion_parent=PARENT,max_ticks=15,sources=[{'kind':'web'}])


class UnifiedTests(TestCase):
    def test_large_source_retains_context_and_dossier_with_small_model_answers(self):
        store=VersionedStore();pages=Pages();api=BudgetAPI(approval());send=LargeBodyHTTP()
        source=restore_source(source_value());text=source.text+'x'*70000
        source=replace(source,text=text,text_hash=hashlib.sha256(text.encode()).hexdigest())
        value=envelope(source,kind='inoreader',source_db_id=2,assessment_ref='fixture')
        model=GeminiModel(KEY,approval(),enabled=True,transport=send,clock=lambda:NOW)
        runner=Coordinator(store,model,PostgresQuota(api,'one-reviewed-run',approval()),
            NotionDelivery(store,'fake',PARENT,enabled=True,transport=pages),clock=lambda:NOW)
        result=runner.run([value],model_enabled=True,notion_enabled=True)
        self.assertEqual(result['deliveries'][0]['state'],'CONFIRMED')
        self.assertEqual(send.calls,['extract','analyze','review'])
        self.assertGreater(max(len(r['result'].output_json.encode()) for r in store._jobs.values()),65536)
        self.assertEqual(len(pages.pages),1)

    def test_feed_web_context_dossier_paid_ledger_and_restart_no_duplicate(self):
        store=VersionedStore();pages=Pages();api=BudgetAPI(approval());send=ModelHTTP()
        def runner():
            model=GeminiModel(KEY,approval(),enabled=True,transport=send,clock=lambda:NOW)
            return Coordinator(store,model,PostgresQuota(api,'one-reviewed-run',approval()),
                               NotionDelivery(store,'fake',PARENT,enabled=True,transport=pages),clock=lambda:NOW)
        a=restore_source(source_value())
        b=replace(a,url='https://example.org/second',item_id='second',source_id='second-source')
        sources=[envelope(a,kind='inoreader',source_db_id=2,assessment_ref='fixture'),
                 envelope(b,kind='web',source_db_id=3,assessment_ref='fixture')]
        result=runner().run(sources,model_enabled=True,notion_enabled=True)
        self.assertEqual(result['submitted'],2)
        self.assertEqual(send.calls,['extract','extract','analyze','analyze','review','review'])
        self.assertEqual(len(pages.pages),2)
        self.assertTrue(all(x['state']=='CONFIRMED' for x in result['deliveries']))
        self.assertEqual(sum(row['charged'] for row in api.rows.values()),3330)
        for packet in store.ready(5):
            self.assertEqual(packet['qa'],{'decision':'HOLD','public_release':False})
            self.assertEqual(packet['context_manifest'][0]['status'],'lead')
            self.assertTrue(packet['dossier']['hypotheses'][0]['refutation'])
        again=runner().run(sources,model_enabled=True,notion_enabled=True)
        self.assertFalse(again['public_release'])
        self.assertEqual(len(send.calls),6);self.assertEqual(sum(m=='POST' for m,u in pages.calls),2)

    def test_old_or_over_budget_config_stops_before_git_or_network(self):
        for change in ('old','shape','cap'):
            c=config()
            if change=='old':c['version']='exp009-runtime-v2'
            if change=='shape':c['free_approval']=c.pop('model_access')
            if change=='cap':c['model_access']['cap_micro_usd']=1000001
            with self.subTest(change=change),patch('collectors.inoreader.probe.verify_revision') as verify:
                with self.assertRaises(ValueError):live_run(c,allow_live=True,expected_sha='a'*40)
                verify.assert_not_called()

    def test_modes_are_explicit_config_choices_not_environment_fallback(self):
        c=config();self.assertEqual(validate_config(c)['model_access']['mode'],'CAPPED_PAID')
        c['model_access']=asdict(approval('FREE_ONLY'))
        self.assertEqual(validate_config(c)['model_access']['mode'],'FREE_ONLY')
        c['model_access']['mode']='AUTO'
        with self.assertRaises(RuntimeError):validate_config(c)

    def test_same_version_concurrent_delivery_only_one_post(self):
        store=Outbox();http=NotionHTTP();entered=Event();release=Event()
        def send(method,url,headers,body=None):
            if method=='POST':
                entered.set()
                if not release.wait(3):raise TimeoutError('TEST_BARRIER')
            return http(method,url,headers,body)
        delivery=NotionDelivery(store,'fake',PARENT,enabled=True,transport=send)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(delivery.deliver,packet_record())
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(DeliveryUnknown):delivery.deliver(packet_record())
            finally:release.set()
            self.assertEqual(first.result(3)['state'],'CONFIRMED')
        self.assertEqual(sum(m=='POST' for m,u in http.calls),1)

    def test_unknown_then_human_edit_reconciliation_never_posts_again(self):
        store=Outbox();http=NotionHTTP();http.lose_ack=True
        delivery=NotionDelivery(store,'fake',PARENT,enabled=True,transport=http)
        with self.assertRaises(DeliveryUnknown):delivery.deliver(packet_record())
        http.edited=True
        with self.assertRaises(DeliveryConflict):delivery.reconcile(packet_record())
        self.assertEqual(store.row['state'],'CONFLICT')
        self.assertEqual(sum(m=='POST' for m,u in http.calls),1)

    def test_changed_dossier_has_different_marker_even_with_same_prose_hash(self):
        from collectors.intelligence_pipeline.notion import page_content
        a=packet_record();b=deepcopy(a);b['packet']['dossier']['gaps'][0]['question']='Different?'
        self.assertEqual(a['content_hash'],b['content_hash'])
        self.assertNotEqual(page_content(a,PARENT)[2],page_content(b,PARENT)[2])

    def test_http_notion_readback_query_is_preserved_and_credentials_url_is_rejected(self):
        url='https://api.notion.com/v1/blocks/id/children?page_size=100'
        with patch('collectors.intelligence_pipeline.gemini.build_opener') as factory:
            response=factory.return_value.open.return_value.__enter__.return_value
            response.geturl.return_value=url;response.read.return_value=b'{"has_more":false}'
            http_json('GET',url,{})
            self.assertEqual(factory.return_value.open.call_args.args[0].full_url,url)
            self.assertEqual(factory.call_args.args[1].proxies,{})
        for url in ['https://key@api.notion.com/v1/pages','https://api.notion.com:8443/v1/pages',
                    'https://api.notion.com/v1/pages?unexpected=1','https://other.example/v1/pages']:
            with self.subTest(url=url),patch('collectors.intelligence_pipeline.gemini.build_opener') as factory:
                with self.assertRaises(ValueError):http_json('POST',url,{}, {})
                factory.assert_not_called()
