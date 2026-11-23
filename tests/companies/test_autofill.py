"""Autonomous card filling, step 3.5 (B4CE-04). No network, no database."""
import io
import json
import pathlib
import unittest
from datetime import date

from collectors.companies import autofill as A

FIX = pathlib.Path(__file__).parent / 'fixtures'


def q(cid, route='open_register', tier=None, refresh=False, country='FI', last_event=None):
    return {'company_id': cid, 'name': cid, 'country': country, 'route': route, 'tier': tier or route,
            'refresh': refresh, 'last_event': last_event}


class Select(unittest.TestCase):
    TODAY = date(2026, 11, 22)

    def test_only_free_routes_without_card(self):
        queue = [q('a'), q('b', route='no_route'), q('c', refresh=True), q('d', route='lei', country='DE')]
        self.assertEqual([x['company_id'] for x in A.select(queue, {}, self.TODAY, 10)], ['a', 'd'])

    def test_recent_attempt_waits_unless_a_newer_event(self):
        queue = [q('a'), q('b', tier='event', last_event='2026-11-20'), q('c', tier='event', last_event='2026-11-01')]
        attempts = {'a': '2026-11-10', 'b': '2026-11-10', 'c': '2026-11-10'}
        self.assertEqual([x['company_id'] for x in A.select(queue, attempts, self.TODAY, 10)], ['b'])
        self.assertEqual([x['company_id'] for x in A.select(queue, {'a': '2026-10-01'}, self.TODAY, 10)], ['b', 'c', 'a'])  # events first

    def test_events_then_companies_with_a_grant_or_award(self):
        queue = [q('a'), q('b'), q('c', tier='event', last_event='2026-11-20'), q('d')]
        self.assertEqual([x['company_id'] for x in A.select(queue, {}, self.TODAY, 10, with_signal={'d'})], ['c', 'd', 'a', 'b'])

    def test_limit_and_country(self):
        queue = [q('a'), q('b', country='CZ'), q('c')]
        self.assertEqual([x['company_id'] for x in A.select(queue, {}, self.TODAY, 1, {'CZ'})], ['b'])


def card(**kw):
    c = {'identity': {'decision': 'matched', 'activity': ['25.40Z']}, 'gate': 'pass', 'level': 'L3',
         'confidence': 'verified', 'rule': 'R03', 'gaps': []}
    c.update(kw)
    return c


class Decide(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(A.decide(card())[0], 'write')
        self.assertEqual(A.decide(card(gate='fail', gaps=['identity: not_found']))[0], 'fail')
        self.assertEqual(A.decide(card(level='L0', confidence='candidate'))[0], 'held_l0')
        self.assertEqual(A.decide(card(identity={'decision': 'error', 'reason': 'URLError'}))[0], 'error')

    def test_restaurant_name_match_is_held(self):  # the AddUp case, France 21.11.2026
        out, why = A.decide(card(identity={'decision': 'matched', 'activity': ['56.10C']}))
        self.assertEqual(out, 'held_activity')
        self.assertIn('food service', why)
        self.assertEqual(A.decide(card(identity={'decision': 'matched', 'activity': ['5610']}))[0], 'held_activity')
        self.assertEqual(A.decide(card(identity={'decision': 'matched', 'activity': ['26.20Z']}))[0], 'write')

    def test_universities_are_held(self):  # Univerzita Karlova, owner 23.11.2026
        out, why = A.decide(card(name='UNIVERZITA KARLOVA', identity={'decision': 'matched', 'activity': ['72100']}))
        self.assertEqual(out, 'held_activity')
        self.assertIn('university', why)
        self.assertEqual(A.decide(card(identity={'decision': 'matched', 'legal_name': 'Politechnika X',
                                                 'activity': ['85.42Z']}))[0], 'held_activity')
        self.assertEqual(A.decide(card(identity={'decision': 'matched', 'legal_name': 'Aalto-yliopisto'}))[0], 'held_activity')
        self.assertEqual(A.decide(card(identity={'decision': 'matched', 'legal_name': 'GISAT s.r.o.',
                                                 'activity': ['471', '63']}))[0], 'write')


class Payload(unittest.TestCase):
    def test_same_claims_as_the_accepted_manual_batch(self):
        cards = json.loads((FIX / 'b4_pl_cards.json').read_text(encoding='utf-8'))
        ref = json.loads((FIX / 'b4_pl_payload.json').read_text(encoding='utf-8'))
        sources, claims = [], []
        for c in cards:
            d = A.payload(c, 'B4CE-04 batch 2 PL 2026-11-22')
            sources += d['sources']
            claims += d['claims']
        self.assertEqual(claims, ref['claims'])
        self.assertEqual(sources, ref['sources'])


class Res:
    def __init__(self, rows): self.rows = rows
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None


class FakeConn:
    """Dry run against fixed rows: one company with an EDF grant whose legal entity already has a card."""
    def __init__(self, twin, carded=()): self.twin, self.carded, self.asked = twin, list(carded), []
    def commit(self): pass
    def execute(self, sql, params=None):
        self.asked.append(sql)
        if sql is A.EXPORT_SQL:
            return Res([{'id': 'co1', 'name': 'ACME Oy', 'country': 'FI', 'lei': False, 'national_id': False,
                         'last_signal': None, 'has_card': False}])
        if sql is A.ATTEMPTS_SQL: return Res([])
        if sql is A.DETAILS_SQL: return Res([{'id': 'co1', 'website': None, 'ids': {}}])
        if sql is A.SIGNALS_SQL:
            return Res([{'company_id': 'co1', 'type': 'grant', 'layer': 'A', 'counterpart': 'EDF', 'date': '2025-01-01',
                         'url': 'https://ec.europa.eu/x/101000001/EDF'}])
        if sql is A.SAME_ENTITY_SQL: return Res([{'company_id': 'co0'}] if self.twin else [])
        if sql is A.CARDED_NAMES_SQL: return Res(self.carded)
        raise AssertionError(sql[:40])


class RunDry(unittest.TestCase):
    def setUp(self):
        self.orig = A.identity, A.load_feed
        A.identity = lambda entry, ids, today: {'decision': 'matched', 'scheme': 'FI_YTUNNUS', 'id': '1234567-8',
                                                 'legal_name': 'ACME Oy', 'activity': [], 'source_url': 'https://x.fi/1'}
        A.load_feed = lambda: []

    def tearDown(self):
        A.identity, A.load_feed = self.orig

    def test_would_write(self):
        out = A.run(FakeConn(False), {}, today=date(2026, 11, 22), log=lambda *_: None)
        self.assertEqual(out['outcomes'], {'would_write': 1})

    def test_second_record_of_a_carded_entity_is_skipped(self):
        out = A.run(FakeConn(True), {}, today=date(2026, 11, 22), log=lambda *_: None)
        self.assertEqual(out['outcomes'], {'skipped': 1})
        self.assertIn('co0', out['held'][0])


class SameName(unittest.TestCase):
    setUp, tearDown = RunDry.setUp, RunDry.tearDown

    def test_card_under_another_record_with_the_same_legal_name_is_skipped(self):  # Česká zbrojovka, 23.11.2026
        conn = FakeConn(False, [{'company_id': 'co9', 'legal_name': 'ACME OY', 'country': 'FI'}])
        out = A.run(conn, {}, today=date(2026, 11, 22), log=lambda *_: None)
        self.assertEqual(out['outcomes'], {'skipped': 1})
        self.assertIn('co9', out['held'][0])

    def test_same_name_in_another_country_is_not_a_twin(self):
        conn = FakeConn(False, [{'company_id': 'co9', 'legal_name': 'ACME Oy', 'country': 'SE'}])
        self.assertEqual(A.run(conn, {}, today=date(2026, 11, 22), log=lambda *_: None)['outcomes'], {'would_write': 1})


class FreeModels(unittest.TestCase):
    def test_chain_free_first_paid_last(self):
        import os
        keep = {k: os.environ.get(k) for k in ('GEMINI_FREE_API_KEY', 'GROQ_API_KEY', 'MISTRAL_API_KEY', 'GEMINI_SEARCH_API_KEY')}
        try:
            for k in keep:
                os.environ.pop(k, None)
            os.environ['GROQ_API_KEY'] = 'g'
            chain = A.model_chain({'GEMINI_API_KEY': 'paid'})
            self.assertEqual([(p[0], p[2]) for p in chain], [('groq', 'g'), ('gemini', 'paid')])
        finally:
            for k, v in keep.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_failed_provider_hands_over(self):
        import urllib.error
        calls = []
        orig = A.P.extract

        def fake(entry, pages, p):
            calls.append(p[0])
            if p[0] == 'paid':
                return {'kept': [1], 'rejected': [], 'provider': 'paid:m'}
            raise urllib.error.HTTPError('u', 402, 'payment', None, None)
        A.P.extract = fake
        try:
            got = A.products_for({}, {'pages': ['p']}, [('free', None, 'k', 'm'), ('paid', None, 'k', 'm')], pause=0)
        finally:
            A.P.extract = orig
        self.assertEqual((calls, got['provider']), (['free', 'paid'], 'paid:m'))


class Whitelist(unittest.TestCase):
    def test_krs_from_nip(self):
        def opener(url, timeout=0):
            self.assertIn('/nip/5261040828', url)
            return io.BytesIO(json.dumps({'result': {'subject': {'krs': '0000012345'}}}).encode())
        self.assertEqual(A.krs_from_nip('526-104-08-28', date(2026, 11, 22), opener), '0000012345')
        self.assertIsNone(A.krs_from_nip('12345', date(2026, 11, 22), opener))  # wrong length: not asked


if __name__ == '__main__':
    unittest.main()
