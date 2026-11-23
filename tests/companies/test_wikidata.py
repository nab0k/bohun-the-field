import io
import json
import unittest
import urllib.parse

from collectors.companies.sources import wikidata as W


def entity(p31=(), p17=(), lei=(), site=(), parent=(), extra=None):
    def snak(v):
        return {'mainsnak': {'datavalue': {'value': v}}}
    claims = {'P31': [snak({'id': x}) for x in p31], 'P17': [snak({'id': x}) for x in p17],
              'P1278': [snak(x) for x in lei], 'P856': [snak(x) for x in site], 'P749': [snak({'id': x}) for x in parent]}
    claims.update(extra or {})
    return {'claims': claims}


class FakeWeb:
    """Answers the three request kinds from canned data; records the URLs."""

    def __init__(self, search, entities, gleif=None):
        self.search, self.entities, self.gleif, self.urls = search, entities, gleif or [], []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        if 'wbsearchentities' in url:
            body = {'search': self.search}
        elif 'wbgetentities' in url:
            ids = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['ids'][0].split('|')
            body = {'entities': {i: self.entities[i] for i in ids if i in self.entities}}
        else:
            body = {'data': self.gleif}
        return io.BytesIO(json.dumps(body).encode())


COUNTRIES = {'Q183': entity(extra={'P297': [{'mainsnak': {'datavalue': {'value': 'DE'}}}]}),
             'Q38': entity(extra={'P297': [{'mainsnak': {'datavalue': {'value': 'IT'}}}]})}


class Wikidata(unittest.TestCase):
    def setUp(self):
        W._ISO.clear()

    def test_company_matched_with_country_lei_site(self):
        web = FakeWeb([{'id': 'Q1', 'label': 'Fincantieri', 'match': {'text': 'Fincantieri'}},
                       {'id': 'Q2', 'label': 'Fincantieri-class corvette'}],
                      {'Q1': entity(['Q4830453'], ['Q38'], ['8156005BDF49128B6239'], ['https://www.fincantieri.com']),
                       **COUNTRIES})
        got = W.wikidata('FINCANTIERI S.p.A.', 'IT', web, pause=0)
        self.assertEqual((got['decision'], got['qid'], got['country'], got['lei'], got['website']),
                         ('matched', 'Q1', 'IT', '8156005BDF49128B6239', 'https://www.fincantieri.com'))

    def test_not_a_business_and_wrong_country(self):
        web = FakeWeb([{'id': 'Q2', 'label': 'Leopard', 'match': {'text': 'Leopard'}}],
                      {'Q2': entity(['Q1186981'], ['Q183']), **COUNTRIES})  # a tank model, not a business
        self.assertEqual(W.wikidata('Leopard', None, web, pause=0)['decision'], 'not_found')
        web = FakeWeb([{'id': 'Q3', 'label': 'Acme', 'match': {'text': 'Acme'}}],
                      {'Q3': entity(['Q4830453'], ['Q183']), **COUNTRIES})
        self.assertEqual(W.wikidata('Acme', 'IT', web, pause=0)['decision'], 'not_found')  # known country disagrees
        self.assertEqual(W.wikidata('Acme', None, web, pause=0)['country'], 'DE')  # unknown country: found one

    def test_two_businesses_same_name_disputed(self):
        web = FakeWeb([{'id': 'Q4', 'label': 'MTU Aero Engines AG', 'match': {'text': 'MTU Aero Engines AG'}},
                       {'id': 'Q5', 'label': 'MTU Aero Engines AG', 'match': {'text': 'MTU Aero Engines AG'}}],
                      {'Q4': entity(['Q4830453'], ['Q183']), 'Q5': entity(['Q4830453'], ['Q183']), **COUNTRIES})
        self.assertEqual(W.wikidata('MTU AERO ENGINES AG', 'DE', web, pause=0)['decision'], 'disputed')

    def test_legal_identifier_makes_a_business(self):
        web = FakeWeb([{'id': 'Q6', 'label': 'Helzel', 'match': {'text': 'Helzel'}}],
                      {'Q6': entity(['Q99999'], ['Q183'], ['529900MQF5CCPV0JUQ10']), **COUNTRIES})
        self.assertEqual(W.wikidata('Helzel GmbH', 'DE', web, pause=0)['decision'], 'matched')


class Gleif(unittest.TestCase):
    def rec(self, lei, name, country='DE'):
        return {'id': lei, 'attributes': {'entity': {'legalName': {'name': name},
                                                     'legalAddress': {'country': country, 'city': 'MUENCHEN'}}}}

    def test_exact_name_only(self):
        web = FakeWeb([], {}, [self.rec('L1', 'Rheinmetall Automotive AG'), self.rec('L2', 'Diehl Defence GmbH & Co. KG')])
        got = W.gleif_by_name('Diehl Defence GmbH & Co. KG', 'DE', web)
        self.assertEqual((got['decision'], got['lei'], got['city']), ('matched', 'L2', 'Muenchen'))
        self.assertIn('legalAddress.country%5D=DE', web.urls[0])
        self.assertEqual(W.gleif_by_name('Rheinmetall AG', 'DE', web)['decision'], 'not_found')

    def test_enrich_takes_lei_from_wikidata(self):
        web = FakeWeb([{'id': 'Q1', 'label': 'Fincantieri', 'match': {'text': 'Fincantieri'}}],
                      {'Q1': entity(['Q4830453'], ['Q38'], ['8156005BDF49128B6239']), **COUNTRIES})
        got = W.enrich('Fincantieri', None, web, pause=0)
        self.assertEqual((got['country'], got['lei']), ('IT', {'decision': 'from_wikidata', 'lei': '8156005BDF49128B6239'}))
        self.assertIn('legalAddress.country%5D=IT', [u for u in web.urls if 'gleif' in u][0])  # the found country

    def test_two_different_leis_are_a_conflict(self):  # Nikon SLM Solutions, 23.11.2026
        web = FakeWeb([{'id': 'Q7', 'label': 'Nikon SLM Solutions AG', 'match': {'text': 'Nikon SLM Solutions AG'}}],
                      {'Q7': entity(['Q4830453'], ['Q183'], ['5299004VIBQF63906C97']), **COUNTRIES},
                      [self.rec('391200JFRHYCWBLA0104', 'Nikon SLM Solutions AG')])
        got = W.enrich('Nikon SLM Solutions AG', 'DE', web, pause=0)
        self.assertEqual(got['lei']['decision'], 'conflict')

    def test_legal_form_kinds_must_agree(self):
        self.assertFalse(W.same_entity_name('Vector Technologies, Inc.', 'Vector Technologies LLC'))
        self.assertTrue(W.same_entity_name('HECKLER & KOCH GmbH', 'Heckler & Koch GmbH'))
        self.assertTrue(W.same_entity_name('Fincantieri', 'FINCANTIERI S.p.A.'))  # one side without a form
        web = FakeWeb([], {}, [self.rec('L3', 'Vector Technologies LLC', 'US')])
        self.assertEqual(W.gleif_by_name('VECTOR TECHNOLOGIES, INC.', 'US', web)['decision'], 'not_found')


if __name__ == '__main__':
    unittest.main()
