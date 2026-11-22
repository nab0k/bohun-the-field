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
        self.assertEqual([x['company_id'] for x in A.select(queue, {'a': '2026-10-01'}, self.TODAY, 10)], ['a', 'b', 'c'])

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


class Whitelist(unittest.TestCase):
    def test_krs_from_nip(self):
        def opener(url, timeout=0):
            self.assertIn('/nip/5261040828', url)
            return io.BytesIO(json.dumps({'result': {'subject': {'krs': '0000012345'}}}).encode())
        self.assertEqual(A.krs_from_nip('526-104-08-28', date(2026, 11, 22), opener), '0000012345')
        self.assertIsNone(A.krs_from_nip('12345', date(2026, 11, 22), opener))  # wrong length: not asked


if __name__ == '__main__':
    unittest.main()
