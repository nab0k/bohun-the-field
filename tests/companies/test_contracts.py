import json
import unittest
from datetime import date

from collectors.companies import contracts as K
from collectors.companies.sources import ca_contracts as C
from collectors.companies.sources import us_contracts as U


class Us(unittest.TestCase):
    def test_match_all_ueis_of_the_exact_name(self):
        rows = [{'Recipient Name': 'LOCKHEED MARTIN CORPORATION', 'Recipient UEI': 'A'}] * 3 + \
               [{'Recipient Name': 'LOCKHEED MARTIN CORP', 'Recipient UEI': 'B'},
                {'Recipient Name': 'LOCKHEED MARTIN AERONAUTICS CO', 'Recipient UEI': 'C'},  # another name: not the same recipient
                {'Recipient Name': 'SHIELD AIR SOLUTIONS INC', 'Recipient UEI': 'D'}]
        m = U.match('Lockheed Martin Corporation', rows)
        self.assertEqual((m['uei'], m['ueis']), ('A', ['A', 'B']))
        self.assertIsNone(U.match('Shield AI', rows))

    def test_title_period(self):
        self.assertEqual(U.title('THE PURPOSE OF THIS MODIFICATION IS TO AWARD F-35A LRIP 15', 'X'), 'F-35A LRIP 15')
        self.assertEqual(U.title('V-BAT VTOL UNMANNED AIRCRAFT', 'X'), 'V-BAT VTOL unmanned aircraft')
        self.assertEqual(U.title('200204!008532!1700!AF600 !NAVAL AIR', 'N0001902C3002'), 'Contract N0001902C3002')
        self.assertEqual(U.period('2024-10-01', '2026-09-30'), 'FY2025–26')
        self.assertEqual(U.period('2025-10-01', '2027-09-30', date(2026, 11, 24)), 'FY2026–27, to 24 Nov 2026')
        self.assertEqual(U.title('THE CONTRACT AWARD IS FOR THE FY21 BASE AND OPTION PRICING', 'X'), 'The contract award is for the FY21 base and option pricing')

    def test_parse_period_money_from_buyers(self):
        rec = {'uei': 'A', 'ueis': ['A'], 'recipient_name': 'SHIELD AI INC'}
        d = U.parse(rec, [{'name': 'Department of the Navy', 'amount': 14.7e6}, {'name': 'Department of the Air Force', 'amount': -1e5}], 6,
                    [{'Award ID': 'N1', 'Recipient UEI': 'A', 'recipient_id': 'h-C', 'Award Amount': 11.3e6, 'Awarding Sub Agency': 'Department of the Navy',
                      'Start Date': '2025-09-03', 'End Date': '2027-12-01', 'Description': 'V-BAT VTOL UNMANNED AIRCRAFT', 'generated_internal_id': 'CONT_N1'}],
                    '2024-10-01', '2026-09-30')
        self.assertEqual((d['total'], d['count'], len(d['buyers'])), (14.7e6, 6, 1))  # a negative de-obligation is not a buyer
        self.assertEqual(d['profile'], 'https://www.usaspending.gov/recipient/h-C/latest')
        self.assertEqual(d['awards'][0]['url'], 'https://www.usaspending.gov/award/CONT_N1')


class Canada(unittest.TestCase):
    def test_variants(self):
        v = C.variants('CAE Inc.')
        for x in ('CAE Inc.', 'CAE INC.', 'CAE', 'CAE Inc', 'CAE INC'):
            self.assertIn(x, v)

    def test_quarterly_repeats_count_once(self):
        row = {'contract_date': '2009-02-12', 'owner_org_title': 'National Defence | Défense nationale', 'owner_org': 'dnd-mdn',
               'description_en': 'Weapons (including parts)', 'contract_value': '714600000.00', 'vendor_name': 'CAE Inc.'}
        rows = [dict(row, reference_number=f'C-2024-2025-Q{q}-1') for q in (1, 2, 3)] + \
               [dict(row, reference_number='C-2019-Q1-2', description_en='Training aids', contract_value='5000', owner_org_title='Transport Canada | x', owner_org='tc')]
        s = C.summarise(rows)
        self.assertEqual((s['count'], s['rows'], s['defence']), (2, 4, 1))
        self.assertEqual(s['contracts'][0]['ref'], 'C-2024-2025-Q3-1')  # the latest report
        self.assertEqual(s['contracts'][0]['url'], 'https://search.open.canada.ca/contracts/record/dnd-mdn,C-2024-2025-Q3-1')
        self.assertIsNone(C.summarise([]))


US = {'recipient': 'SHIELD AI INC', 'uei': 'DVEJ', 'ueis': 1, 'partial': False, 'profile': 'https://www.usaspending.gov/recipient/h/latest',
      'period': 'FY2025–26', 'total': 14.7e6, 'count': 6, 'buyers': [{'name': 'Department of the Navy', 'amount': 14.7e6}],
      'awards': [{'ref': 'N1', 'buyer': 'Department of the Navy', 'title': 'V-BAT', 'amount': 11.3e6, 'date': '2025-09-03', 'end': '2027-12-01',
                  'url': 'https://www.usaspending.gov/award/CONT_N1'}]}


class Step(unittest.TestCase):
    def test_window(self):
        self.assertEqual(K.fiscal_window(date(2026, 11, 24)), ('2025-10-01', '2027-09-30'))
        self.assertEqual(K.fiscal_window(date(2026, 9, 1)), ('2024-10-01', '2026-09-30'))

    def test_card_for_us_company_by_uei_when_no_lei(self):
        t = {'id': 'co1', 'name': 'Shield AI', 'country': 'US', 'lei': None, 'has_card': False}
        found = K.us_claims(US)
        idn = K.identity(t, US)
        self.assertEqual(idn[:2], ('US_UEI', 'DVEJ'))
        data = K.payload(t, found, date(2026, 11, 24), {'id': idn, 'legal_name': 'SHIELD AI INC', 'why': 'DoD'})
        refs = {c['ref'] for c in data['claims']}
        self.assertTrue({'b4:legal_name', 'b4:regno', 'b4:level', 'b4:spend', 'b4:ctr1'} <= refs)
        self.assertEqual({s['ref'] for s in data['sources']}, {'b4:reg'})  # the profile is both the identity and the record
        lvl = next(c for c in data['claims'] if c['ref'] == 'b4:level')['value']
        self.assertEqual((lvl['level'], lvl['confidence'], lvl['rule']), ('L3', 'verified', 'R03'))

    def test_lei_wins_and_canada_needs_defence(self):
        t = {'id': 'co2', 'name': 'CAE Inc.', 'country': 'CA', 'lei': '5493', 'has_card': False}
        self.assertEqual(K.identity(t, {})[0], 'LEI')
        self.assertIsNone(K.identity(dict(t, lei=None), {'uei': 'x', 'profile': 'https://x'}))  # Canada: no UEI route

    def test_existing_card_gets_contracts_only(self):
        t = {'id': 'co3', 'name': 'RAYTHEON COMPANY', 'country': 'US', 'lei': None, 'has_card': True}
        data = K.payload(t, K.us_claims(US), date(2026, 11, 24))
        self.assertFalse({'b4:legal_name', 'b4:regno', 'b4:level'} & {c['ref'] for c in data['claims']})
        self.assertTrue(all(c['ref'].startswith(K.TAGS) for c in data['claims']))

    def test_unchanged(self):
        found = K.us_claims(US)
        self.assertTrue(K.same([{'claim_ref': k, 'claim_value': json.loads(json.dumps(v))} for k, v in found.items()], found))
        self.assertFalse(K.same([], found))


class Attempts(unittest.TestCase):
    def test_outcomes_fit_the_table_check(self):  # 25.11.2026: CheckViolation on card_attempt
        allowed = {'written', 'fail', 'held_l0', 'held_activity', 'error', 'skipped', 'refreshed', 'unchanged'}
        self.assertTrue(set(K.DB_OUTCOME.values()) <= allowed)
        for o in ('card_made', 'written', 'refreshed', 'unchanged', 'no_record', 'no_identity', 'error'):
            self.assertIn(o, K.DB_OUTCOME)

    def test_run_logs_allowed_outcomes(self):
        class Conn:
            def __init__(self):
                self.rows = []

            def execute(self, sql, params=None):
                if 'insert into companies.card_attempt' in sql:
                    self.rows.append(params)
                class R:
                    def fetchall(s):
                        return [{'id': 'co1', 'name': 'Nobody Inc', 'country': 'US', 'has_card': False, 'legal_name': None, 'lei': None, 'size': 3}] \
                            if 'with c as' in sql else []
                return R()

            def transaction(self):
                import contextlib
                return contextlib.nullcontext()
        c = Conn()
        s = K.run(c, write=True, today=date(2026, 11, 25), us_fetch=lambda *a, **k: None, log=lambda *m: None)
        self.assertEqual(s['outcomes'], {'no_record': 1})
        self.assertEqual(c.rows[0][2], 'skipped')
        self.assertTrue(c.rows[0][4].startswith('no_record'))


class Sql(unittest.TestCase):
    def test_no_stray_percent(self):  # 24.11.2026: "like 'B4CE-04 contracts%'" broke every pass with ProgrammingError
        import re
        from collectors.companies import graph as G
        for mod in (K, G):
            for name in dir(mod):
                if name.endswith('_SQL'):
                    sql = getattr(mod, name)
                    self.assertEqual(re.findall(r"%(?!\(\w+\)s|%|s\b)", sql.replace('%%', '')), [], f'{mod.__name__}.{name}')


if __name__ == '__main__':
    unittest.main()
