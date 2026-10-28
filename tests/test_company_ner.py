"""Contract/linking tests, no model installation, source network or database."""
import copy
import unittest
from collectors.company_ner import contracts as C
from collectors.company_ner.catalog import project_catalog
from collectors.company_ner.linker import link_documents


def row(text=None, ident='1'):
    text = text or ('Acme announced a contract. ' * 12)
    return {'item_id': ident, 'title': 'A source', 'url': 'https://example.org/news',
            'published_at': '2026-10-27T00:00:00+00:00', 'source_updated_at': '2026-10-27T01:00:00+00:00',
            'content_capture': {'version':1, 'status':'BODY_PRESENT_COMPLETENESS_UNVERIFIED',
              'text':text, 'text_bytes':len(text.encode()), 'text_sha256':C.sha(text)}}


def answer(text, name='Acme'):
    a = text.index(name)
    return {'entities':{'organization':[{'text':name,'start':a,'end':a+len(name),'confidence':0.9}]}}


def catalog():
    return [{'id':'co_one','name':'Acme Ltd.','country':'GB','status':'card',
             'notion_page_id':None,'updated_at':'2026-10-27T00:00:00+00:00','aliases':[]}]


class ContractTests(unittest.TestCase):
    def test_body_hash_rejects_changed_source(self):
        r=row();r['content_capture']['text'] += 'x'
        with self.assertRaisesRegex(ValueError,'BODY_HASH'):C.source(r)

    def test_identity_depends_on_text_and_model_schema(self):
        text=row()['content_capture']['text'];before=C.job_key(text)
        for key in ['model','schema','options','adapter']:
            changed=copy.deepcopy(C.CONTRACT);changed[key]={'changed':True}
            self.assertNotEqual(before,C.job_key(text,changed))
        self.assertNotEqual(before,C.job_key(text+'x'))

    def test_source_id_does_not_change_computation_key(self):
        a,b=C.source(row(ident='1')),C.source(row(ident='2'))
        self.assertNotEqual(a['id'],b['id']);self.assertEqual(C.job_key(a['text']),C.job_key(b['text']))
        self.assertEqual(a['completeness'],'UNKNOWN');self.assertFalse(a['public_release'])

    def test_wrong_offset_and_nonfinite_confidence_rejected(self):
        text=row()['content_capture']['text']
        for field,value in [('end',3),('start',True),('confidence',float('nan')),('confidence',1.1)]:
            bad=answer(text);bad['entities']['organization'][0][field]=value
            with self.assertRaises(ValueError):C.predictions(text,bad)

    def test_source_alignment_is_not_semantic_approval(self):
        text=('Britain announced a programme. '*12)
        result=C.predictions(text,answer(text,'Britain'))
        self.assertEqual(result['entities']['organization'][0]['text'],'Britain')
        # Deliberate limitation: source checks cannot tell country from company.
        companies=project_catalog(catalog());doc=C.source(row(text))
        linked=link_documents([doc],{doc['id']:{'entities':result}},companies)
        self.assertEqual(linked['proposed_news_company_refs'],[])
        self.assertFalse(linked['mentions'][0]['identity_confirmed'])

    def test_ambiguous_names_remain_two_candidates(self):
        companies=catalog()+[{**catalog()[0],'id':'co_two','name':'Acme PLC'}]
        doc=C.source(row());result=link_documents([doc],{doc['id']:{'entities':answer(doc['text'])}},project_catalog(companies))
        self.assertEqual(len(result['proposed_news_company_refs']),2)
        self.assertEqual(result['mentions'][0]['status'],'multiple_name_candidates')
        self.assertFalse(result['catalog_mutations']);self.assertEqual(result['confirmed_links'],[])

    def test_excluded_cards_and_footer_not_linked(self):
        text=('Acme article and other source text. '*10)+'The post appeared first on Acme.'
        doc=C.source(row(text));a=text.rindex('Acme');pred={'entities':{'organization':[{
            'text':'Acme','start':a,'end':a+4,'confidence':0.9}]}}
        result=link_documents([doc],{doc['id']:{'entities':pred}},project_catalog(catalog()))
        self.assertEqual(result['mentions'][0]['status'],'source_credit_only')
        self.assertFalse(result['proposed_news_company_refs'])
        self.assertEqual(project_catalog([{**catalog()[0],'status':'excluded'}]),[])

    def test_parent_or_subsidiary_similarity_is_not_link(self):
        doc=C.source(row('Elbit Systems confirmed a contract. '*10))
        companies=[{**catalog()[0],'name':'Elbit Systems Sweden AB'}]
        result=link_documents([doc],{doc['id']:{'entities':answer(doc['text'],'Elbit Systems')}},project_catalog(companies))
        self.assertFalse(result['proposed_news_company_refs'])
        self.assertEqual(len(result['related_name_candidates']),1)


if __name__=='__main__':unittest.main()
