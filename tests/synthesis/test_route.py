import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

from collectors.synthesis.contracts import Document, Resources, canonical, sha
from collectors.synthesis.extraction import extract, modality, money
from collectors.synthesis.editorial import event_match, is_primary_url, primary_candidates
from collectors.synthesis.route import run
from collectors.synthesis.store import MemoryStore
from collectors.synthesis.writer import validate, request, FixtureWriter
from collectors.synthesis.offline import private_output, evaluate


def resources():
    # Synthetic editorial configuration; real A-18 files and private corpus are
    # passed only to the separate replay CLI, never copied into these tests.
    d = {'modality': {'negated':['dropped','cancelled','will not','did not'],
        'completed':['completed','achieved'], 'delivered':['delivered'],
        'signed':['signed','awarded','agreement'], 'announced':['announced'],
        'planned':['plans to','explore','will','memorandum of cooperation']},
        'commitment': {'approval':['approved a possible','DSCA'], 'framework':['framework agreement'],
          'ceiling':['up to'], 'funded':['grant of'], 'delivered':['delivered'], 'contract':['contract']},
        'key_facts_by_event_type': {'contract':['participant','object','amount_or_not_disclosed'],
          'production':['participant','object'], 'funding':['participant','object','amount_or_not_disclosed'],
          'partnership':['participant','partner','object'],'policy':['jurisdiction','object']},
        'not_disclosed_markers':['not disclosed','undisclosed'],
        'banned_words_unless_quoted':['largest','breakthrough'], 'forecast_markers':['could lead to','will likely'],
        'hard_bans_extra': {'ukraine_location_markers':['enterprise in','region enterprise']},
        'primary_source_domains': {'government_suffixes':['.gov','.mil','.gov.uk'], 'official_hosts':['www.nato.int']}}
    t = {'version':'topics-v1','topics':[{'slug':'sensors','title':'Sensor contracts','priority':1,'min_hits':2,'keywords':['radar','sensor','Aster Labs']}]}
    schema = {'type':'object','properties':{'context':{'$ref':'#/$defs/sentences','maxItems':3}},'$defs':{'sentences':{'type':'array','items':{'type':'object'}}}}
    prompt='```text\nSynthetic test instructions\n```\n```json\n{}\n```\n```json\n'+json.dumps(schema)+'\n```'
    return Resources(t,d,prompt)


def doc(text=None, **kwargs):
    title='Aster Labs awarded $20 million contract for radar sensor modules'
    data={'id':'d1','title':title,'text':text or title+'\nAster Labs signed a contract for radar sensor modules.',
          'url':'https://example.org/article','published_at':'2026-10-19T12:00:00+00:00','outlet':'Synthetic outlet','source_class':'P1'}
    data.update(kwargs)
    return Document(**data)


def packet():
    return {'facts':[{'id':'F1','block':'what_happened','text':'Aster Labs was awarded a contract for radar sensor modules.',
                      'quote':'Aster Labs was awarded a contract for radar sensor modules.','modality':'signed','commitment':'contract','amounts':[]}],
            'sources':[],'event':{}}


def output():
    return {'headline':{'text':'Aster Labs awarded radar contract','fact_ids':['F1']},
            'what_happened':[{'text':'Aster Labs was awarded a radar contract.','fact_ids':['F1']}],
            'context':[],'policy_direction':[],'bigger_picture':[],'supply_chain':[],
            'why_it_matters':[{'text':'The contract covers radar modules.','fact_ids':['F1']}]}


class ExtractionTests(unittest.TestCase):
    def test_quotes_have_exact_offsets_and_hash(self):
        d=doc(); a=extract(d,resources())
        self.assertFalse(a['missing'])
        for f in a['facts']:
            self.assertEqual(d.text[f['quote_start']:f['quote_end']],f['quote'])
            self.assertLessEqual(len(f['quote']),200)
            self.assertEqual(sha(f['quote']),f['quote_sha256'])
            self.assertEqual(sha(d.text),f['artifact_sha256'])

    def test_long_sentence_values_outside_quote_never_attached(self):
        d=doc('Aster Labs signed a sensor contract '+('extra details '*30)+'worth $500 million.')
        for f in extract(d,resources())['facts']:
            for a in f['amounts']:self.assertIn(a['literal'],f['quote'])

    def test_parent_programme_sum_does_not_fill_child_award(self):
        d=doc('Aster Labs awarded funding for radar sensor modules\nREAD: The agency awarded $20 million for 16 unrelated projects.', title='Aster Labs awarded funding for radar sensor modules')
        self.assertIn('amount_or_not_disclosed',extract(d,resources())['missing'])

    def test_sidebar_sum_and_child_in_same_window_not_child_amount(self):
        d=doc('Aster Labs awarded funding for radar sensor modules\nREAD: Canada invests $20 million in 16 projects Aster Labs develops sensors.', title='Aster Labs awarded funding for radar sensor modules')
        self.assertIn('amount_or_not_disclosed',extract(d,resources())['missing'])

    def test_joint_venture_name_does_not_override_contract_action(self):
        d=doc(title='Aster Joint Venture receives $20 million contract for radar modules')
        self.assertEqual(extract(d,resources())['type'],'contract')

    def test_currency_does_not_infer_usd_from_bare_dollars(self):
        self.assertEqual(money('$20 million')[0]['currency'],'DOLLAR_UNSPECIFIED')
        self.assertEqual(money('CAD$20 million')[0]['currency'],'CAD')
        self.assertEqual(money('930 millions de dollars')[0]['currency'],'DOLLAR_UNSPECIFIED')

    def test_negation_priority(self):
        self.assertEqual(modality('The order was cancelled after it was signed.',resources().dictionaries)[0],'negated')

    def test_signed_memorandum_to_explore_is_still_plan(self):
        self.assertEqual(modality('They signed a Memorandum of Cooperation to explore production.',resources().dictionaries)[0],'planned')

    def test_unknown_does_not_become_confirmation(self):
        self.assertEqual(modality('A sensor exists.',resources().dictionaries)[0],'unknown')

    def test_names_remain_candidates(self):
        a=extract(doc(),resources(),[{'id':'c1','name':'Aster Labs','country':'US'}])
        self.assertTrue(a['companies'])
        self.assertTrue(all(c['company_id'] is None and c['link_status']=='candidate' for c in a['companies']))

    def test_ner_spans_bound_to_text(self):
        with self.assertRaises(ValueError):extract(doc(),resources(),ner_spans=[{'start':0,'end':4,'text':'FAKE'}])

    def test_input_https_required(self):
        with self.assertRaises(ValueError):doc(url='http://example.org')


class RouteTests(unittest.TestCase):
    def test_offline_no_network_and_no_public_release(self):
        with patch.object(socket,'socket',side_effect=AssertionError('NETWORK')):
            a=run([doc()],resources())
        self.assertEqual(a['events'][0]['admission']['decision'],'HOLD')
        self.assertIn('writer_not_run',a['events'][0]['admission']['reason_codes'])
        self.assertFalse(a['events'][0]['admission']['public_release'])
        self.assertEqual(a['model_calls'],0)

    def test_missing_facts_prevents_writer(self):
        d=doc('Aster Labs signed a contract for radar sensor modules.',title='Aster Labs signed radar contract')
        with patch('collectors.synthesis.route.request',side_effect=AssertionError('WRITER')):
            a=run([d],resources())
        self.assertIn('extraction_gap',a['events'][0]['admission']['reason_codes'])

    def test_fixture_writer_and_admission_end_to_end_stays_simulated(self):
        def writer(req):
            value=output()
            for item in [value['headline'],*value['what_happened'],*value['why_it_matters']]:
                item['fact_ids']=[req['packet']['facts'][0]['id']]
            return value
        a=run([doc()],resources(),writer=writer)
        self.assertEqual(a['events'][0]['admission']['decision'],'PUBLISH')
        self.assertFalse(a['events'][0]['admission']['public_release'])
        self.assertTrue(a['events'][0]['admission']['simulation_only'])

    def test_context_never_uses_past_synthesis_as_fact(self):
        from collectors.synthesis.context import context_packet
        snap={'context':[{'id':'C1','topic':'sensors','origin':'synthesis','quote':'A claim','source_text':'A claim','date':'2026-10-18T00:00:00+00:00'}]}
        self.assertEqual(context_packet(snap,'sensors','2026-10-19T00:00:00+00:00'),[])

    def test_military_ukrainian_location_rejected_before_writer(self):
        d=doc('A report describes an enterprise in Kyiv.',title='Attack on Kyiv region enterprise kills people')
        a=run([d],resources(),writer=lambda _:self.fail('writer called'))
        self.assertEqual(a['events'][0]['admission']['decision'],'REJECT')
        self.assertIn('hard_ban:ukraine_location',a['events'][0]['admission']['reason_codes'])

    def test_topic_missing_holds(self):
        r=resources(); r.topics['topics'][0]['keywords']=['not in source']
        self.assertIn('topic_missing',run([doc()],r)['events'][0]['admission']['reason_codes'])

    def test_unknown_source_is_not_p1(self):
        self.assertIn('source_unassessed',run([doc(source_class='unknown')],resources())['events'][0]['admission']['reason_codes'])

    def test_exact_reprints_deduplicated(self):
        a=run([doc(),doc(id='d2')],resources())
        self.assertEqual(a['duplicate_document_ids'],['d2'])
        self.assertEqual(len(a['events']),1)

    def test_replay_deterministic_and_store_idempotent(self):
        store=MemoryStore();a=run([doc()],resources(),store=store);b=run([doc()],resources(),store=store)
        self.assertEqual(canonical(a),canonical(b));self.assertEqual(next(iter(store.events.values()))['version'],1)

    def test_human_or_hidden_version_protected(self):
        store=MemoryStore();run([doc()],resources(),store=store);key=next(iter(store.events))
        old=copy.deepcopy(store.events[key]);store.events[key]['active_origin']='human'
        row=copy.deepcopy(old['result']);row['type']='production'
        self.assertEqual(store.save(key,row,'v1'),'PROTECTED')
        self.assertEqual(store.events[key]['version'],1)

    def test_updated_same_document_creates_version_in_memory_not_new_event(self):
        store=MemoryStore();run([doc()],resources(),store=store)
        run([doc(doc().text+' Additional source detail.')],resources(),store=store)
        self.assertEqual(len(store.events),1)
        self.assertEqual(next(iter(store.events.values()))['version'],2)

    def test_primary_link_host_boundary(self):
        d=resources().dictionaries
        self.assertTrue(is_primary_url('https://agency.gov/a',d))
        self.assertFalse(is_primary_url('https://agency.gov.evil.test/a',d))
        x=doc('See https://agency.gov/a and https://agency.gov/b and https://agency.gov/c')
        self.assertEqual(len(primary_candidates(x,d)),2)

    def test_weak_similarity_never_merges(self):
        a={'type':'contract','subject':'aster labs','object_key':'AB-1','published_at':'2026-10-19T00:00:00+00:00','amount':None}
        self.assertIsNone(event_match(a,a))

    def test_hard_feature_matching_window_and_amount(self):
        a={'type':'contract','subject':'aster labs','object_key':'AB-1','published_at':'2026-10-19T00:00:00+00:00','amount':{'value':'20','currency':'EUR'}}
        self.assertEqual(event_match(a,a),'hard_features')
        b=copy.deepcopy(a);b['amount']['value']='40';self.assertIsNone(event_match(a,b))
        b=copy.deepcopy(a);b['published_at']='2026-01-19T00:00:00+00:00';self.assertIsNone(event_match(a,b))

    def test_private_output_refuses_public_repo(self):
        with self.assertRaises(ValueError):private_output(Path(__file__).resolve().parents[2]/'output')


class WriterTests(unittest.TestCase):
    def check_bad(self,mutator,code):
        o=output();p=packet();mutator(o,p)
        self.assertIn(code,validate(o,p,resources().dictionaries)['codes'])

    def test_valid_fixture(self):self.assertTrue(validate(output(),packet(),resources().dictionaries)['ok'])
    def test_unknown_fact(self):self.check_bad(lambda o,p:o['headline'].update(fact_ids=['UNKNOWN']),'writer_schema_or_fact_id')
    def test_unsupported_number(self):self.check_bad(lambda o,p:o['what_happened'][0].update(text='Aster Labs was awarded 99 radar modules.'),'unsupported_number')
    def test_wrong_block(self):self.check_bad(lambda o,p:o['context'].append(o['what_happened'][0]),'wrong_fact_block')
    def test_lost_negation(self):self.check_bad(lambda o,p:p['facts'][0].update(modality='negated'),'negation_lost')
    def test_strengthened_plan(self):self.check_bad(lambda o,p:p['facts'][0].update(modality='planned'),'modality_strengthened')
    def test_forecast(self):self.check_bad(lambda o,p:o['why_it_matters'][0].update(text='This could lead to more orders.'),'forecast')
    def test_hype(self):self.check_bad(lambda o,p:o['headline'].update(text='Aster Labs achieves breakthrough'),'hype')
    def test_unsupported_name(self):self.check_bad(lambda o,p:o['headline'].update(text='Invented Industries awarded radar contract'),'unsupported_name')
    def test_ceiling_lost(self):self.check_bad(lambda o,p:(p['facts'][0].update(commitment='ceiling',text=p['facts'][0]['text']+' 20'),o['what_happened'][0].update(text='The contract covers 20 modules.')),'ceiling_lost')
    def test_approval_not_contract(self):self.check_bad(lambda o,p:p['facts'][0].update(commitment='approval'),'approval_as_contract')
    def test_long_verbatim_quote(self):
        p=packet();p['facts'][0]['quote']=' '.join(['word']*14);o=output();o['why_it_matters'][0]['text']=p['facts'][0]['quote']
        self.assertIn('verbatim_13_words',validate(o,p,resources().dictionaries)['codes'])
    def test_schema_expands_refs(self):self.assertNotIn('$ref',canonical(request(packet(),resources())['schema']))
    def test_fixture_bound_to_hash(self):
        w=FixtureWriter({sha(canonical(packet())):output()});self.assertEqual(w(request(packet(),resources())),output())
        p=packet();p['facts'][0]['text']='Different'
        with self.assertRaises(ValueError):w(request(p,resources()))
    def test_attribution_and_why_from_context_pass(self):
        p = packet(); p['sources'] = [{'outlet': 'Defence Industry Europe'}]
        p['facts'].append({'id': 'D1', 'block': 'policy_direction', 'text': 'Radar budgets rise.', 'quote': 'Radar budgets rise.', 'modality': 'announced'})
        o = output(); o['what_happened'] = [{'text': 'According to Defence Industry Europe, Aster Labs was awarded a radar contract.', 'fact_ids': ['F1']}]
        o['why_it_matters'] = [{'text': 'Radar budgets rise.', 'fact_ids': ['D1']}]
        self.assertEqual(validate(o, p, resources().dictionaries)['codes'], [])
    def test_missing_writer_blocks(self):self.assertFalse(validate({},packet(),resources().dictionaries)['ok'])


class EditorialV2(unittest.TestCase):
    def v2(self):
        r=resources()
        t={'version':'topics-v2','topics':[
            {'slug':'sensors','title':'Sensor contracts','priority':2,'min_hits':2,'min_strong':1,'keywords':['radar','sensor'],'weak_keywords':['Aster Labs','contract']},
            {'slug':'firms','title':'Firm news','priority':1,'min_hits':2,'min_strong':1,'keywords':['shipyard'],'weak_keywords':['Aster Labs','contract']}]}
        d=copy.deepcopy(r.dictionaries)
        d['primary_source_domains']['company_hosts_seed']=['asterlabs.com']
        d['source_classes']={'P1':['defensenews.com'],'P2':['defence-industry.eu']}
        return Resources(t,d,r.prompt)
    def test_weak_words_alone_do_not_qualify(self):
        from collectors.synthesis.editorial import select_topic
        got=select_topic('Aster Labs contract news',[],self.v2().topics)
        self.assertIsNone(got['main'])
    def test_strong_word_needed_and_ranked_first(self):
        from collectors.synthesis.editorial import select_topic
        got=select_topic('Aster Labs radar contract',[],self.v2().topics)
        self.assertEqual(got['main']['slug'],'sensors'); self.assertEqual([t['slug'] for t in got['all']],['sensors'])
    def test_source_classes_from_domains(self):
        from collectors.synthesis.editorial import source_class
        r=self.v2().dictionaries
        self.assertEqual(source_class(doc(url='https://www.defensenews.com/x',source_class='unknown'),r),'P1')
        self.assertEqual(source_class(doc(url='https://defence-industry.eu/x',source_class='unknown'),r),'P2')
        self.assertEqual(source_class(doc(url='https://news.asterlabs.com/x',source_class='unknown'),r),'unknown')
        self.assertEqual(source_class(doc(url='https://asterlabs.com/x',source_class='unknown'),r),'P0')
        self.assertEqual(source_class(doc(url='https://example.org/x',source_class='unknown'),r),'unknown')
    def test_p2_only_holds(self):
        res=run([doc(url='https://defence-industry.eu/x',source_class='unknown')],self.v2())
        self.assertIn('p2_only',res['events'][0]['admission']['reason_codes'])
    def test_unclear_duplicates_one_to_writer(self):
        from collectors.synthesis.editorial import duplicate_clusters
        g=[{'title':'Calian Awarded $47M Contract for NATO Joint Warfare Centre','date':'2026-10-19T18:00:00+00:00','rank':(1,-1)},
           {'title':'Calian awarded contract for NATO Joint Warfare Centre scenario development','date':'2026-10-19T21:00:00+00:00','rank':(0,-1)},
           {'title':'Calian opens office in Ottawa','date':'2026-10-19T21:00:00+00:00','rank':(0,-1)},
           {'title':'Calian Awarded Contract for NATO Joint Warfare Centre','date':'2026-11-19T21:00:00+00:00','rank':(0,-1)}]
        self.assertEqual(duplicate_clusters(g),{0:1,1:1,2:2,3:3})
    def test_duplicate_candidate_not_written(self):
        a=doc(); b=doc(id='d2',url='https://example.org/other',text='Aster Labs radar sensor modules deal confirmed by the buyer.')
        w=FixtureWriter({})
        res=run([a,b],self.v2(),writer=w)
        codes=[e['admission']['reason_codes'] for e in res['events']]
        self.assertEqual(sum('duplicate_candidate' in c for c in codes),1)
    def test_empty_why_holds(self):
        p=packet(); o=output(); o['why_it_matters']=[]
        self.assertTrue(validate(o,p,resources().dictionaries)['ok'])


class StreamTitleTests(unittest.TestCase):
    """Headline cues and subjects taken from the 27-28.10 stream (titles only, no source text)."""
    def test_types(self):
        from collectors.synthesis.extraction import event_type
        cases = {"Germany Drops Boeing MQ-28 Ghost Bat Rapid Procurement Plan": "policy",
                 "Pentagon, BAE sign deal for up to 25,000 precision-guided munition kits a year": "contract",
                 "Panzerabwehr – Finnland beschafft RGW110 von Dynamit Nobel Defence": "contract",
                 "Hanwha Defense USA to Acquire Hanwha Philly Shipyard": "partnership",
                 "Nominal Secures $71M to Expand Across Collaborative Combat Aircraft Testing": "funding",
                 "Navantia launches Spanish Navy's second F110 frigate four months ahead of schedule": "production",
                 "Where Are The Aircraft Carriers?: October 27, 2026": "other",
                 "Dozens of Former Airmen Are Running in the '26 Midterms": "other"}
        for title, kind in cases.items():
            self.assertEqual(event_type(title, ""), kind, title)
    def test_subjects(self):
        from collectors.synthesis.extraction import title_subject
        cases = {"Geospatial intelligence contract goes to CGI": "CGI",
                 "BNET-V – Dänemark beschafft Funkgeräte von DND Digital": "Dänemark",
                 "► Chess Dynamics secures additional contract with PT Len": "Chess Dynamics",
                 "Weibel to supply XENTA-M5 radars for Poland's SAN programme": "Weibel",
                 "Where Are The Aircraft Carriers?: October 27, 2026": None}
        for title, subject in cases.items():
            self.assertEqual(title_subject(title), subject, title)


if __name__=='__main__':unittest.main()
