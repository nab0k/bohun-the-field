"""Level classifier against the two owner-approved test sets (B2CI-03).

Expected answers are read from the fixtures unchanged. This file only translates each case into
input claims for collectors.companies.level.classify.
"""
import json
import os
import unittest

from collectors.companies.level import classify, revenue_share

FIX = os.path.join(os.path.dirname(__file__), 'fixtures')

GOV = {'source': 'government', 'group': 'gov', 'named': True}
SECOND = {'source': 'second_party', 'group': 'p2', 'named': True}
SELF = {'source': 'self', 'group': 'self'}


def cl(kind, **kw):
    d = {'kind': kind, 'documented': True}
    d.update(kw)
    return d


# Synthetic scenarios T01-T30: predicate -> claims (+ entity_resolved)
T_INPUT = {
    'official_specialized_finished_product': [cl('product', specialized=True, **GOV)],
    'official_specialized_component_named_program': [cl('component', specialized=True, **GOV)],
    'general_product_documented_relevance_no_customer': [cl('relevance', **SELF)],
    'general_product_completed_documented_engagement': [cl('contract', customer=True, historical=True, **GOV)],
    'documented_specialized_component': [cl('component', specialized=True, **SECOND)],
    'general_consumer_goods_marketing_only': [cl('marketing', **SELF)],
    'official_award_for_general_product': [cl('award', customer=True, **GOV)],
    'general_service_catalog_no_customer': [cl('generic_material', **SELF)],
    'general_service_documented_contract': [cl('contract', customer=True, **GOV)],
    'specialized_software_role_documented': [cl('product', specialized=True, **SECOND)],
    'general_logistics_documented_customer': [cl('contract', customer=True, service_kind='logistics', **GOV)],
    'standard_recruitment_documented_customer': [cl('contract', customer=True, service_kind='recruitment', **GOV)],
    'financial_intermediary_documented_transaction': [cl('contract', customer=True, service_kind='finance', **GOV)],
    'affiliated_entity_has_distinct_activity': [cl('affiliate', **GOV)],
    'bidder_only': [cl('bid', **GOV)],
    'award_no_execution_evidence': [cl('award', customer=True, **GOV)],
    'regulatory_product_flag_no_customer': [cl('regulatory_flag', **GOV)],
    'same_name_different_official_ids': [cl('identity_conflict', **GOV)],
    'five_reprints_share_one_source': [cl('marketing', source='media', group='release-1') for _ in range(5)],
    'documented_end_of_engagement': [cl('end_of_engagement', **GOV)],
    'conflicting_dates_and_counterparty_ids': [cl('identity_conflict', **GOV)],
    'conditional_capability_statement': [cl('capability_claim', **SELF)],
    'documented_specialized_service': [cl('service', specialized=True, **SECOND)],
    'generic_material_no_specific_relevance': [cl('generic_material', **SELF)],
    'ordinary_facility_cleaning_customer_public': [cl('contract', customer=True, service_kind='cleaning', **GOV)],
    'division_mentioned_no_direct_evidence': [cl('division_mention', **SELF)],
    'civilian_use_only_model_has_prior_belief': [cl('generic_material', **SELF)],
    'same_brand_distinct_legal_entities_one_engagement': [cl('same_brand_entities', **GOV)],
    'different_numerator_denominator_years': [],
    'notice_no_winner': [cl('notice', **GOV)],
}

# Real companies R01-R30: claims from the cards (sources in the fixture evidence)
P1 = lambda g: {'source': 'government', 'group': g, 'named': True}
P2 = lambda g, named=True: {'source': 'second_party', 'group': g, 'named': named}
MEDIA = lambda g: {'source': 'media', 'group': g, 'named': True}
OWN = lambda named=True: {'source': 'self', 'group': 'self', 'named': named}

R_INPUT = {
    'R01': ([cl('product', specialized=True, **P2('leonardo')), cl('affiliate', **OWN())], True),
    'R02': ([cl('product', specialized=True, **P1('uk-mod'))], True),
    'R03': ([cl('product', specialized=True, **P2('hensoldt'))], True),
    'R04': ([cl('product', specialized=True, **P1('fi-mod'))], True),
    'R05': ([cl('product', specialized=True, **P1('us-gov'))], True),
    'R06': ([cl('product', specialized=True, **P2('naval-group')), cl('affiliate', **OWN())], True),
    'R07': ([cl('product', specialized=True, **P2('volvo', named=False))], True),
    'R08': ([cl('component', specialized=True, **P1('cz-mod')), cl('product', specialized=True, **OWN(False))], True),
    'R09': ([cl('product', specialized=True, **OWN())], True),
    'R10': ([cl('product', specialized=True, **OWN()), cl('same_brand_entities', **P1('gleif'))], False),
    'R11': ([cl('product', specialized=True, **MEDIA('orf'))], False),
    'R12': ([cl('product', specialized=True, **P1('pl-gov'))], True),
    'R13': ([cl('contract', customer=True, **P1('pl-mod')), cl('product', specialized=True, **OWN(False))], True),
    'R14': ([cl('relevance', **P1('fi-mod')), cl('component', specialized=True, **OWN(False))], True),
    'R15': ([cl('generic_material', **OWN()), cl('membership', **OWN())], True),
    'R16': ([cl('non_defence_project', flag='law_enforcement', **P1('eu')), cl('marketing', **OWN())], True),
    'R17': ([cl('contract', customer=True, **MEDIA('kivi'))], False),
    'R18': ([cl('capability_claim', **OWN())], False),
    'R19': ([cl('capability_claim', **OWN())], False),
    'R20': ([cl('capability_claim', **OWN())], True),
    'R21': ([cl('capability_claim', **OWN()), cl('membership', **OWN())], True),
    'R22': ([cl('product', specialized=True, **P2('hensoldt'))], False),
    'R23': ([cl('product', specialized=True, **OWN())], False),
    'R24': ([cl('product', specialized=True, **OWN()), cl('affiliate', **P2('hensoldt'))], False),
    'R25': ([cl('product', specialized=True, **P2('leonardo')), cl('product', specialized=True, **P2('hensoldt'))], False),
    'R26': ([cl('product', specialized=True, **P1('cz-mod')), cl('product', specialized=True, **P2('hensoldt'))], False),
    'R27': ([cl('product', specialized=True, **P2('kongsberg'))], False),
    'R28': ([cl('contract', customer=True, **P1('pl-mod'))], False),
    'R29': ([cl('contract', customer=True, **P1('pl-mod'))], False),
    'R30': ([cl('affiliate', **OWN())], True),
}

CONF_COMPAT = {k: {k} for k in ('unknown', 'candidate', 'supported', 'verified', 'disputed')}


def load(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return json.load(f)


class TestSyntheticT(unittest.TestCase):
    def test_all_thirty(self):
        cases = load('level_T01-T30.yaml')
        self.assertEqual(len(cases), 30)
        for case in cases:
            pred = case['input_claims'][0]['predicate']
            with self.subTest(case=case['id'], predicate=pred):
                self.assertIn(pred, T_INPUT, 'no input mapping')
                exp = case['expected']
                if pred == 'different_numerator_denominator_years':
                    r = revenue_share({'amount': 50, 'year': 2024, 'currency': 'EUR', 'scope': 'group'},
                                      {'amount': 200, 'year': 2025, 'currency': 'EUR', 'scope': 'group'})
                    self.assertEqual(r['share'], exp['revenue_share'])
                    self.assertIn('inputs', r)
                    continue
                out = classify(T_INPUT[pred])
                self.assertTrue(out['explanation'] or out['rule'] or out['ignored'] or out['flags'])
                if 'level' in exp:
                    self.assertEqual(out['level'], exp['level'])
                if 'allowed_levels' in exp:
                    self.assertIn(out['level'], exp['allowed_levels'])
                for bad in exp.get('prohibited_levels', []):
                    self.assertNotEqual(out['level'], bad)
                if 'confidence' in exp:
                    self.assertIn(out['confidence'], CONF_COMPAT[exp['confidence']])
                if 'time_class' in exp:
                    self.assertEqual(out['time_class'], exp['time_class'])
                if 'event_stage' in exp:
                    self.assertEqual(out['event_stage'], exp['event_stage'])
                if exp.get('manual_review'):
                    self.assertTrue(out['manual_review'])
                if exp.get('regulatory_flag'):
                    self.assertIn('regulatory_flag', out['flags'])
                if exp.get('distinct_legal_entities'):
                    self.assertIn('distinct_legal_entities', out['flags'])
                if 'independence_group_count' in exp:
                    self.assertEqual(out['independence_group_count'], exp['independence_group_count'])
                if 'inherit_affiliate_classification' in case.get('forbidden_claims', []):
                    self.assertEqual(out['level'], 'L0')
                    self.assertTrue(out['ignored'])


class TestRealR(unittest.TestCase):
    def test_all_thirty(self):
        data = load('level_real_R01-R30.yaml')
        self.assertEqual(len(data['cases']), 30)
        for case in data['cases']:
            with self.subTest(case=case['id'], entity=case['entity']):
                claims, resolved = R_INPUT[case['id']]
                out = classify(claims, entity_resolved=resolved)
                exp = case['expected']
                self.assertEqual(out['level'], exp['level'])
                self.assertEqual(out['confidence'], exp['confidence'])
                if exp['must_explain']:
                    self.assertTrue(out['explanation'])


class TestRevenueShare(unittest.TestCase):
    def test_comparable(self):
        r = revenue_share({'amount': 1407, 'year': 2025, 'currency': 'EUR', 'scope': 'group'},
                          {'amount': 5457, 'year': 2025, 'currency': 'EUR', 'scope': 'group'})
        self.assertAlmostEqual(r['share'], 0.2578, places=4)

    def test_zero_denominator_is_unknown(self):
        self.assertEqual(revenue_share({'amount': 1, 'year': 1, 'currency': 'EUR', 'scope': 'g'},
                                       {'amount': 0, 'year': 1, 'currency': 'EUR', 'scope': 'g'})['share'], 'unknown')


if __name__ == '__main__':
    unittest.main()
