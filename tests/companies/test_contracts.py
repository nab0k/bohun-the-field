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
        data = K.payload(t, found, date(2026, 11, 24), {'id': idn, 'legal_name': 'SHIELD AI INC', 'why': 'DoD', 'city': 'San Diego'})
        refs = {c['ref'] for c in data['claims']}
        self.assertTrue({'b4:legal_name', 'b4:regno', 'b4:city', 'b4:level', 'b4:spend', 'b4:ctr1'} <= refs)
        self.assertEqual(next(c for c in data['claims'] if c['ref'] == 'b4:city')['value'], 'San Diego')
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
                        return [{'id': 'co1', 'name': 'Nobody Inc', 'country': 'US', 'has_card': False, 'has_city': False, 'legal_name': None, 'lei': None, 'size': 3}] \
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
        self.assertTrue(c.rows[0][4].endswith(K.MARK))  # the re-check under the defence codes happens once
        self.assertIn("a.outcome <> 'error'", K.TARGETS_SQL)  # a failed re-check is tried again (Lockheed, 25.11.2026)


class DefenceCodes(unittest.TestCase):  # owner 25.11.2026 «Yes, defence codes only»
    def opener(self, defence, every):
        calls = []

        class R:
            def __init__(s, body): s.body = body
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return json.dumps(s.body).encode()

        def op(req, timeout=None):
            body = json.loads(req.data) if req.data else {}
            calls.append((req.full_url, body))
            url = req.full_url
            if url.endswith('/search/spending_by_award/') and body.get('fields') == ['Recipient Name', 'Recipient UEI']:
                return R({'results': [{'Recipient Name': 'CDW GOVERNMENT LLC', 'Recipient UEI': 'U1'}]})
            if url.endswith('/spending_by_category/recipient/'):
                psc = 'psc_codes' in body['filters']
                return R({'results': [{'name': 'CDW GOVERNMENT LLC', 'amount': defence if psc else every}]})
            if url.endswith('/awarding_subagency/'):
                return R({'results': [{'name': 'Department of the Army', 'amount': defence}]})
            if url.endswith('/spending_by_award_count/'):
                return R({'results': {'contracts': 3}})
            if url.endswith('/search/spending_by_award/'):
                return R({'results': []})
            return R({})
        return op, calls

    def test_mostly_non_defence_purchases_give_no_summary(self):  # CDW Government, 6 % on 25.11.2026
        op, calls = self.opener(2.6e6, 40.6e6)
        d = U.fetch('CDW Government LLC', '2025-10-01', '2027-09-30', opener=op)
        self.assertTrue(d['non_defence'])
        self.assertEqual((d['total'], d['defence_share']), (0, 0.064))
        self.assertFalse(any(u.endswith('/awarding_subagency/') for u, _ in calls))

    def test_defence_supplier_counts_only_the_defence_codes(self):  # Lockheed Martin, 85 %
        op, calls = self.opener(40.1e9, 47.2e9)
        d = U.fetch('CDW Government LLC', '2025-10-01', '2027-09-30', opener=op)
        self.assertNotIn('non_defence', d)
        self.assertEqual(d['total'], 40.1e9)
        later = [b for u, b in calls if u.endswith(('/awarding_subagency/', '/spending_by_award_count/'))]
        self.assertTrue(later and all(b['filters']['psc_codes'] == U.PSC for b in later))
        self.assertIn(['Product', '25'], U.PSC['require'])  # vehicle components (LOC Performance)
        claim = K.us_claims(dict(US, defence_share=0.85, all_dod=47.2e9))['b4:spend']
        self.assertEqual((claim['scope'], claim['defence_share']), (K.SCOPE_US, 0.85))

    def test_canada_defence_buyer_and_category(self):
        dnd = {'owner_org_title': 'National Defence | Défense nationale'}
        self.assertTrue(C.defence_contract(dict(dnd, description_en='Weapons (including parts)')))
        self.assertTrue(C.defence_contract(dict(dnd, description_en='Military (R&D)')))
        for cat in ('Fruits and Vegetables', 'Rental of aircraft', 'Office Furniture', 'Floor and Wall Tiling Work'):
            self.assertFalse(C.defence_contract(dict(dnd, description_en=cat)), cat)
        self.assertFalse(C.defence_contract({'owner_org_title': 'Transport Canada | x', 'description_en': 'Aircraft'}))

    def test_card_made_from_non_defence_contracts_is_withdrawn(self):
        class Conn:
            def __init__(self, by_contracts):
                self.sql, self.by = [], by_contracts

            def execute(self, sql, params=None):
                self.sql.append(sql)
                by = self.by

                class R:
                    def fetchall(s):
                        if 'with c as' in sql:
                            return [{'id': 'co1', 'name': 'CDW Government', 'country': 'US', 'has_card': True, 'has_city': True,
                                     'legal_name': None, 'lei': None, 'size': 3, 'by_contracts': by}]
                        return [{'claim_ref': 'b4:spend', 'claim_value': {}}] if sql is K.NOW_SQL else []
                return R()

            def transaction(self):
                import contextlib
                return contextlib.nullcontext()
        fetch = lambda *a, **k: {'recipient': 'CDW GOVERNMENT LLC', 'uei': 'U1', 'total': 0, 'non_defence': True, 'defence_share': 0.06}  # noqa: E731
        c = Conn(True)
        s = K.run(c, write=True, today=date(2026, 11, 25), us_fetch=fetch, log=lambda *m: None)
        self.assertEqual((s['outcomes'], s['withdrawn']), ({'withdrawn': 1}, ['US CDW Government']))
        self.assertIn(K.RETIRE_ALL_SQL, c.sql)
        c = Conn(False)  # a card made elsewhere loses only its contract claims
        s = K.run(c, write=True, today=date(2026, 11, 25), us_fetch=fetch, log=lambda *m: None)
        self.assertEqual((s['outcomes'], s['withdrawn']), ({'withdrawn': 1}, []))
        self.assertIn(K.RETIRE_SQL, c.sql)
        self.assertNotIn(K.RETIRE_ALL_SQL, c.sql)
        self.assertEqual(K.DB_OUTCOME['withdrawn'], 'skipped')


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
