"""Slice 2 (Czechia): AOBP parser, ARES matching, card gate (B1CD-02)."""
import unittest

from collectors.companies.country_slice import card, claims_for, list_key
from collectors.companies.sources import ares
from collectors.companies.sources.aobp import parse

PAGE = ('<div class="member"><div class="member__title"><h2>Zbraně a munice</h2></div><div class="member__list">'
        '<div class="member__item" data-member="acme-as"><a href="/cs/companies/1/acme-as/"><figure><figcaption>'
        '<p class="title">ACME, a.s.</p><p>Výrobce &amp; dodavatel.</p></figcaption></figure></a></div></div></div>')


class TestAobp(unittest.TestCase):
    def test_parse(self):
        rows = parse(PAGE)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['name_as_listed'], 'ACME, a.s.')
        self.assertEqual(rows[0]['specialisations'], ['Zbraně a munice'])
        self.assertEqual(rows[0]['evidence_quote'], 'Výrobce & dodavatel.')
        self.assertTrue(rows[0]['list_row_ref'].startswith('https://www.aobp.cz/'))


class TestAres(unittest.TestCase):
    def test_exact_normalised_only(self):
        res = [{'ico': '1', 'obchodniJmeno': 'AKM GROUP - CZ, a.s.'}, {'ico': '2', 'obchodniJmeno': 'AKM Trade s.r.o.'}]
        self.assertEqual(ares.match('AKM GROUP-CZ, a.s.', res)['ico'], '1')
        self.assertEqual(ares.match('AKM', res)['decision'], 'not_found')

    def test_two_equal_names_disputed(self):
        res = [{'ico': '1', 'obchodniJmeno': 'X s.r.o.'}, {'ico': '2', 'obchodniJmeno': 'X, s.r.o.'}]
        self.assertEqual(ares.match('X s.r.o.', res)['decision'], 'disputed')


class TestCard(unittest.TestCase):
    def test_defence_award_gives_l3(self):
        ev = {'ted': [{'notice': '1-2026', 'buyer': 'Ministerstvo obrany', 'date': '2026-01-01', 'url': 'u',
                       'defence_buyer': True}]}
        c = card('A s.r.o.', {'decision': 'matched', 'ico': '1'}, ev)
        self.assertEqual((c['level'], c['confidence'], c['gate']), ('L3', 'verified', 'pass'))

    def test_membership_only_is_candidate(self):
        c = card('B s.r.o.', {'decision': 'matched', 'ico': '2'}, {'aobp': {'evidence_quote': 'q'}})
        self.assertEqual((c['level'], c['confidence']), ('L4', 'candidate'))
        self.assertEqual(c['gate'], 'pass')  # honest low level with a search record

    def test_unmatched_identity_fails_gate(self):
        c = card('C s.r.o.', {'decision': 'not_found'}, {'aobp': {'evidence_quote': 'q'}})
        self.assertEqual(c['gate'], 'fail')

    def test_list_key(self):
        self.assertEqual(list_key('PRAGA – Export s.r.o.'), list_key('PRAGA-Export s.r.o.'))
        self.assertEqual(claims_for({}), [])


if __name__ == '__main__':
    unittest.main()
