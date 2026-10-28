"""Synthetic evidence and fake HTTP only. No claims of live provider/Notion quality."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
from io import BytesIO
from urllib.error import HTTPError
from unittest import TestCase
from unittest.mock import Mock
from threading import Lock

from collectors.intelligence_pipeline.contracts import digest,validate_dossier
from collectors.intelligence_pipeline.gemini import GeminiModel,FreeApproval,ModelAccessStop,build_request
from collectors.intelligence_pipeline.notion import NotionDelivery,DeliveryUnknown,page_content
from collectors.intelligence_pipeline.editorial import MaterialVersion
from collectors.intelligence_pipeline.publication import approved_preview
from collectors.intelligence_pipeline.retrieval import InternalRetriever,context_record
from collectors.intelligence_pipeline.source_adapters import Assessment,envelope,restore_source,inoreader_source
from collectors.intelligence_pipeline.research_packet import SourceRecord
from test_pipeline import document,responses

NOW=datetime(2026,10,19,12,tzinfo=timezone.utc)
PARENT='e3f13d60-cde4-4949-a8fe-b175e9fadff5';PAGE='81976af5-6683-44a8-8f77-0436748feab8'


def source_value(eligible=True):
    d=document()
    s=SourceRecord('fixture-source','version1',d.item_id,d.url,d.title,d.published_at,d.fetched_at,
                   d.text,d.text_hash,'announced', 'COMPLETE' if eligible else 'UNKNOWN',
                   'PERMITTED' if eligible else 'UNKNOWN',not eligible)
    return envelope(s,kind='web',source_db_id=3,assessment_ref='fixture-assessment')


def dossier():
    return {'methods':['ChangeIntelligence','ACH'],'objects':['Example Works'],
            'change':{'before':'UNKNOWN','after':'Announced plan','demand_type':'unknown'},
            'observations':[{'statement':'A plan is reported.','claim_ids':['c1'],'context_refs':[]}],
            'hypotheses':[{'statement':'Capacity may change.','claim_ids':['c1'],'context_refs':[],
                           'counterhypothesis':'Replacement of existing capacity.','confirmation':'Dated output report.',
                           'refutation':'Plan cancellation.','review_at':'2026-12-31T00:00:00Z'}],
            'forecasts':[],'gaps':[{'question':'Baseline?','scope':'supplied documents','status':'NOT_SEARCHED'}]}


def packet_record():
    d=document();r=responses(d)
    draft=r[d.identity+':analyze']['payload']
    version=MaterialVersion('fixture-material',1,draft['title'],draft['body'],(d.text_hash,),'machine')
    packet={'material_id':version.material_id,'draft':draft,'dossier':dossier(),
            'claims':r[d.identity+':extract']['payload']['claims'],
            'sources':[{'url':d.url,'text_hash':d.text_hash}], 'context_manifest':[], 'coverage':{},
            'qa':{'decision':'HOLD','public_release':False}}
    return {'packet':packet,'material':asdict(version),'content_hash':version.content_hash}


class Outbox:
    def __init__(self):
        self.row={'state':'PENDING','page_id':None};self.marks=[];self.lock=Lock()
    def delivery(self,*args): return dict(self.row)
    def claim_delivery(self,*args,**kwargs):
        with self.lock:
            if self.row['state']!='PENDING':return dict(self.row,claimed=False,token=None)
            self.row={'state':'SENDING','page_id':None,'token':'fixture-token'};self.marks.append('SENDING')
            return dict(self.row,claimed=True)
    def mark_delivery(self,*args,**kwargs):
        with self.lock:
            from collectors.intelligence_pipeline.ports import VersionConflict
            if self.row['state']=='SENDING' and kwargs.get('token')!=self.row['token']:
                raise VersionConflict('STALE_DELIVERY')
            if self.row['state'] not in {'SENDING','UNKNOWN'} and not (self.row['state']=='CONFIRMED' and args[2]=='CONFLICT'):
                raise VersionConflict('DELIVERY_STATE')
            self.row={'state':args[2],'page_id':kwargs.get('page_id')};self.marks.append(args[2])


class NotionHTTP:
    def __init__(self): self.body=None;self.calls=[];self.lose_ack=False;self.edited=False;self.more=False
    def __call__(self,method,url,headers,body=None):
        self.calls.append((method,url))
        if method=='POST':
            self.body=deepcopy(body)
            if self.lose_ack: raise TimeoutError('PRIVATE_BODY_SENTINEL')
            return {'id':PAGE}
        if url.endswith('pages/'+PAGE): return {'properties':self.body['properties'],'parent':self.body['parent']}
        if url.endswith('blocks/'+PARENT+'/children?page_size=100'):
            title=self.body['properties']['title']['title'][0]['text']['content']
            return {'has_more':False,'results':[{'id':PAGE,'type':'child_page','child_page':{'title':title}}]}
        blocks=deepcopy(self.body['children'])
        if self.edited: blocks[2]['paragraph']['rich_text'][0]['text']['content']='HUMAN EDIT'
        for block in blocks: block['has_children']=False
        return {'has_more':self.more,'results':blocks}


class EvidenceBoundaryTests(TestCase):
    def test_source_import_rejects_forged_flags_and_hash(self):
        value=source_value();value['source']['text']='forged'
        with self.assertRaises(ValueError): restore_source(value)
        value=source_value();value['qa']={'decision':'APPROVE'}
        with self.assertRaises(ValueError): restore_source(value)
    def test_assessment_is_hash_bound_and_unknown_default_is_blocked(self):
        source=restore_source(source_value(False))
        self.assertTrue(source.blockers)
        with self.assertRaises(ValueError): Assessment('other',reference='ref').apply(source)
    def test_context_time_hash_and_lead_status_are_enforced(self):
        raw={'id':'lead:1','kind':'company_lead','status':'lead','source_url':'https://example.org/',
             'available_at':'2026-10-18T00:00:00Z','text':'Example','text_hash':hashlib.sha256(b'Example').hexdigest(),
             'quote':'Example','root_id':'lead:1'}
        self.assertEqual(context_record(raw,NOW)['status'],'lead')
        for change in ({'status':'verified'},{'available_at':'2027-01-01T00:00:00Z'}, {'quote':'not literal'},{'text_hash':'0'*64}):
            with self.subTest(change=change),self.assertRaises(ValueError): context_record(raw|change,NOW)
        backend=Mock();backend.context.return_value=[raw]
        result=InternalRetriever(backend).retrieve(document(),as_of=NOW)
        self.assertFalse(result['coverage']['market_absence_proven'])
        self.assertEqual(result['coverage']['distinct_reported_roots'],0)
    def test_dossier_requires_falsifier_and_excludes_probabilities(self):
        claims=[{'id':'c1'}];validate_dossier(dossier(),claims,[])
        for mutation in ('unsupported','probability','falsifier','absence'):
            v=dossier()
            if mutation=='unsupported': v['hypotheses'][0]['claim_ids']=['invented']
            if mutation=='probability': v['hypotheses'][0]['probability']=0.87
            if mutation=='falsifier': v['hypotheses'][0]['refutation']=''
            if mutation=='absence': v['gaps'][0]['status']='MARKET_DOES_NOT_EXIST'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError): validate_dossier(v,claims,[])


class GeminiTests(TestCase):
    def model(self,transport,**kw):
        key='synthetic-key'
        approval=FreeApproval('fixture-account','fixture-free-proof',hashlib.sha256(key.encode()).hexdigest(),'2026-10-20T00:00:00Z')
        return GeminiModel(key,approval,enabled=True,transport=transport,clock=lambda:NOW,**kw)
    def payload(self): return {'document':document().to_dict()}
    def reply(self):
        import json
        p=responses(document())[document().identity+':extract']['payload']
        return {'modelVersion':'gemini-3.5-flash-lite','serviceTier':'standard',
                'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':20,'thoughtsTokenCount':3,'totalTokenCount':33},
                'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(p)}]}}]}
    def test_exact_fixed_request_and_usage_without_double_count(self):
        send=Mock(return_value=self.reply());m=self.model(send);p=self.payload()
        result=m.execute('extract',p,'request',m.estimate('extract',p))
        self.assertEqual((result.usage.input_tokens,result.usage.output_tokens),(10,23))
        method,url,headers,body=send.call_args.args
        self.assertEqual(method,'POST');self.assertNotIn('synthetic-key',url)
        self.assertNotIn('tools',body);self.assertEqual(body['generationConfig']['candidateCount'],1)
        self.assertNotIn('countTokens',url);send.assert_called_once()
    def test_unverified_key_expired_free_or_disabled_never_calls(self):
        send=Mock();m=self.model(send)
        for state in ('key','expired','disabled'):
            x=self.model(send)
            if state=='key': x.key='another-key'
            if state=='expired': x.clock=lambda:datetime(2027,1,1,tzinfo=timezone.utc)
            if state=='disabled': x.enabled=False
            with self.subTest(state=state),self.assertRaises(ModelAccessStop): x.estimate('extract',self.payload())
        send.assert_not_called()
    def test_free_quota_error_has_no_retry(self):
        send=Mock(side_effect=HTTPError('https://example.org',429,'PRIVATE',{},None));m=self.model(send);p=self.payload()
        with self.assertRaises(ModelAccessStop): m.execute('extract',p,'request',m.estimate('extract',p))
        send.assert_called_once()
    def test_thought_metadata_is_not_parsed_as_final_json_and_usage_must_balance(self):
        r=self.reply();parts=r['candidates'][0]['content']['parts']
        parts[0]['thoughtSignature']='opaque-signature'
        parts.insert(0,{'text':'Internal thought is not the result.','thought':True})
        m=self.model(Mock(return_value=r));p=self.payload()
        result=m.execute('extract',p,'request',m.estimate('extract',p))
        self.assertIn('claims',result.payload)
        r['usageMetadata']['totalTokenCount']=32
        with self.assertRaises(RuntimeError): m.execute('extract',p,'request2',m.estimate('extract',p))
    def test_missing_usage_or_incomplete_result_is_uncertain_not_zero(self):
        for change in ('usage','finish','tool'):
            r=self.reply()
            if change=='usage': del r['usageMetadata']['promptTokenCount']
            if change=='finish': r['candidates'][0]['finishReason']='MAX_TOKENS'
            if change=='tool': r['candidates'][0]['content']['parts']=[{'functionCall':{'name':'bad'}}]
            m=self.model(Mock(return_value=r));p=self.payload()
            with self.subTest(change=change),self.assertRaises(RuntimeError): m.execute('extract',p,'request',m.estimate('extract',p))
    def test_method_routing_and_review_hash_covers_dossier(self):
        import json
        p=self.payload()|{'requires_dossier':True,'research_category':'product','draft':packet_record()['packet']['draft'],'dossier':dossier()}
        request=build_request('review',p,100)
        data=json.loads(request['contents'][0]['parts'][0]['text'])
        self.assertIn('IPIA',data['methods'])
        self.assertEqual(data['review_hash'],digest({'draft':p['draft'],'dossier':p['dossier']}))


class NotionTests(TestCase):
    def test_changed_version_body_is_rejected_before_outbox_and_http(self):
        record=packet_record();record['material']['body']='Unreviewed replacement'
        outbox=Mock();send=Mock();delivery=NotionDelivery(outbox,'fake',PARENT,enabled=True,transport=send)
        with self.assertRaises(ValueError): delivery.deliver(record)
        outbox.delivery.assert_not_called();send.assert_not_called()
    def test_success_reads_back_and_duplicate_does_not_post(self):
        outbox=Outbox();http=NotionHTTP();delivery=NotionDelivery(outbox,'fake',PARENT,enabled=True,transport=http)
        record=packet_record();delivery.deliver(record);delivery.deliver(record)
        self.assertEqual([m for m,u in http.calls].count('POST'),1)
        self.assertEqual(outbox.marks,['SENDING','CONFIRMED'])
    def test_post_ack_loss_recovers_by_read_without_repost(self):
        outbox=Outbox();http=NotionHTTP();http.lose_ack=True
        delivery=NotionDelivery(outbox,'fake',PARENT,enabled=True,transport=http);record=packet_record()
        with self.assertRaises(DeliveryUnknown): delivery.deliver(record)
        with self.assertRaises(DeliveryUnknown): delivery.deliver(record)
        self.assertEqual(delivery.reconcile(record)['state'],'CONFIRMED')
        self.assertEqual([m for m,u in http.calls].count('POST'),1)
    def test_human_edit_or_truncated_read_never_confirms_or_overwrites(self):
        for kind in ('edit','truncated'):
            http=NotionHTTP();http.edited=kind=='edit';http.more=kind=='truncated';outbox=Outbox()
            delivery=NotionDelivery(outbox,'fake',PARENT,enabled=True,transport=http)
            with self.subTest(kind=kind),self.assertRaises(DeliveryUnknown): delivery.deliver(packet_record())
            self.assertEqual(outbox.row['state'],'CONFLICT' if kind=='edit' else 'UNKNOWN')
            self.assertNotIn('PATCH',[m for m,u in http.calls])
    def test_invalid_large_page_fails_before_outbox_or_network(self):
        record=packet_record();record['packet']['dossier']={'huge':'x'*200000}
        outbox=Mock();send=Mock();delivery=NotionDelivery(outbox,'fake',PARENT,enabled=True,transport=send)
        with self.assertRaises(ValueError): delivery.deliver(record)
        outbox.delivery.assert_not_called();send.assert_not_called()


class PublicPreviewTests(TestCase):
    def raw(self):
        r=packet_record();m=r['material'];h=r['content_hash']
        return {'material':m,'editorial':{'material_id':m['material_id'],'version':1,'content_hash':h,'decision':'APPROVE'},
                'qa':{'content_hash':h,'version':1,'decision':'APPROVE','public_release':True,'independent_check_ok':True,
                      'evidence_check_ok':True,'rights_verified':True,'sensitive':False,'checker_id':'human-qa'},
                'geography':{'country':'DE','longitude':10,'latitude':51,'kind':'country_centroid','evidence_ref':document().url,'confirmed':True},
                'source_urls':[document().url]}
    def test_disabled_and_hold_are_absent_on_both_outputs(self):
        raw=self.raw();self.assertEqual(approved_preview([raw])['items'],[])
        raw['qa']['decision']='HOLD'
        result=approved_preview([raw],authorized=True,trusted_checkers={'human-qa'})
        self.assertEqual(result['items'],[]);self.assertEqual(result['map']['features'],[])
    def test_same_version_in_feed_and_map_and_no_stale_approval(self):
        raw=self.raw();r=approved_preview([raw],authorized=True,trusted_checkers={'human-qa'})
        self.assertEqual(r['items'][0]['content_hash'],r['map']['features'][0]['properties']['content_hash'])
        raw['material']['body']='Human changed text'
        self.assertEqual(approved_preview([raw],authorized=True,trusted_checkers={'human-qa'})['items'],[])
    def test_sensitive_ua_or_facility_point_is_refused(self):
        for update in ({'country':'UA'},{'kind':'facility'},{'confirmed':False}):
            raw=self.raw();raw['geography'].update(update)
            with self.subTest(update=update),self.assertRaises(ValueError): approved_preview([raw],authorized=True,trusted_checkers={'human-qa'})
