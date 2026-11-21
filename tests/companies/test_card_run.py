"""One card from the queue, any country (B4CE-04, slice 3.4). No network."""
import io
import json
import unittest

from collectors.companies.card_run import card, claims_from_signals
from collectors.companies.sources import registries as R

CO = {'id': 'co1', 'name': 'ACME Oy', 'country': 'FI', 'website': 'https://acme.example/'}
MATCHED = {'decision': 'matched', 'scheme': 'FI_YTUNNUS', 'id': '1234567-8', 'legal_name': 'ACME Oy'}
EDF = {'type': 'grant', 'layer': 'A', 'counterpart': 'European Commission, European Defence Fund', 'date': '2025-01-01',
       'url': 'https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/projects-details/1'}
CIVIL = {'type': 'tender_award', 'layer': 'C', 'counterpart': 'Police', 'date': '2025-02-01',
         'url': 'https://ted.europa.eu/en/notice/-/detail/1-2025'}
NO_LINK = {'type': 'subcontract', 'layer': 'A', 'counterpart': 'OSHKOSH DEFENSE, LLC', 'date': '2025-09-30', 'url': None}
MEMBER = {'type': 'association', 'layer': 'D', 'counterpart': 'association', 'url': 'https://soff.se/x'}
PRODUCT = {'kept': [{'name': 'ACME Falcon', 'kind': 'product', 'specialized': True, 'quote': 'ACME Falcon tactical radar',
                     'url': 'https://acme.example/p'}], 'rejected': [], 'provider': 'gemini:m'}


class TestSignals(unittest.TestCase):
    def test_only_linked_signals_are_cited(self):
        claims, cited = claims_from_signals([EDF, CIVIL, NO_LINK, MEMBER])
        self.assertEqual([c['url'] for c in cited], [EDF['url'], CIVIL['url']])
        self.assertEqual([c['kind'] for c in claims], ['contract', 'relevance', 'membership'])

    def test_us_contract_cited_through_recipient_page(self):
        dod = {'type': 'contract', 'layer': 'A', 'counterpart': 'US Department of Defense', 'date': None, 'url': None}
        self.assertEqual(claims_from_signals([dod])[1], [])
        claims, cited = claims_from_signals([dod], {'USASPENDING_RECIPIENT': 'abc-C'})
        self.assertEqual(cited[0]['url'], 'https://www.usaspending.gov/recipient/abc-C/latest')
        self.assertEqual(claims[0]['kind'], 'contract')


class TestCard(unittest.TestCase):
    def test_edf_grant_gives_l3_and_passes(self):
        c = card(CO, MATCHED, [EDF])
        self.assertEqual((c['level'], c['gate']), ('L3', 'pass'))

    def test_product_lifts_to_l1(self):
        c = card(CO, MATCHED, [EDF], PRODUCT, {'pages': [{'url': 'https://acme.example/p', 'text': '...'}], 'skipped': []})
        self.assertEqual((c['level'], c['rule']), ('L1', 'R01'))
        self.assertEqual(c['empty']['products'], 'assessed')

    def test_unmatched_identity_fails_gate(self):
        c = card(CO, {'decision': 'not_found', 'reason': 'no free register API for DE'}, [EDF])
        self.assertEqual(c['gate'], 'fail')
        self.assertIn('no free register', c['gaps'][0])

    def test_membership_only_is_honest_l0(self):
        c = card(CO, MATCHED, [MEMBER, NO_LINK])
        self.assertEqual((c['level'], c['gate'], c['evidence']), ('L0', 'pass', []))
        self.assertIn('N02', c['ignored'][0])


class TestLei(unittest.TestCase):
    def opener(self, payload):
        return lambda req, timeout=None: io.BytesIO(json.dumps(payload).encode())

    def test_lei_name_must_match(self):
        rec = {'data': {'id': 'LEI1', 'attributes': {'entity': {'legalName': {'name': 'RAYTHEON COMPANY'}, 'status': 'ACTIVE',
               'legalAddress': {'city': 'ARLINGTON'}, 'registeredAs': '123', 'creationDate': '1922-07-07T00:00:00Z'}}}}
        r = R.resolve_lei('Raytheon Company', 'LEI1', self.opener(rec))
        self.assertEqual((r['decision'], r['founded'], r['city'], r['registered_as']), ('matched', '1922-07-07', 'Arlington', '123'))
        self.assertEqual(R.resolve_lei('RTX Corporation', 'LEI1', self.opener(rec))['decision'], 'not_found')

    def test_inactive_lei(self):
        rec = {'data': {'id': 'L', 'attributes': {'entity': {'legalName': {'name': 'X AG'}, 'status': 'INACTIVE'}}}}
        self.assertIn('not active', R.resolve_lei('X AG', 'L', self.opener(rec))['reason'])


if __name__ == '__main__':
    unittest.main()
