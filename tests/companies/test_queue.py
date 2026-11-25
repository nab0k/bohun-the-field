"""Company work queue (B4CE-04, slice 3.2)."""
import unittest
from datetime import date

from collectors.companies.queue import build, mention_key, route

TODAY = date(2026, 11, 21)


def co(i, name, country, **kw):
    return {'id': i, 'name': name, 'country': country, 'lei': False, 'national_id': False,
            'last_signal': None, 'has_card': False, **kw}


def syn(iid, d, text, status='approved'):
    return {'id': iid, 'date': d, 'status': status, 'title': text, 'summary': '', 'paragraphs': []}


class TestMentionKey(unittest.TestCase):
    def test_short_and_generic_names_are_not_matched(self):
        self.assertIsNone(mention_key('ERA a.s.'))          # 'era' would hit 'era of' in any text
        self.assertIsNone(mention_key('Defence Systems AS'))
        self.assertEqual(mention_key('Patria Oyj'), ('patria',))
        self.assertIsNone(mention_key('KG Canada'))  # reduces to a country name


class TestRoute(unittest.TestCase):
    def test_routes(self):
        self.assertEqual(route(co(1, 'A', 'FI'))[0], 'open_register')
        self.assertEqual(route(co(1, 'A', 'PL'))[0], 'no_route')
        self.assertEqual(route(co(1, 'A', 'PL', lei=True))[0], 'open_register')
        self.assertEqual(route(co(1, 'A', 'DE', lei=True))[0], 'lei')
        self.assertEqual(route(co(1, 'A', 'DE'))[0], 'no_route')
        self.assertEqual(route(co(1, 'A', None))[0], 'no_country')


class TestBuild(unittest.TestCase):
    def test_order_and_reasons(self):
        companies = [
            co('de', 'Diehl Defence GmbH', 'DE'),
            co('fi', 'Patria Oyj', 'FI'),
            co('lei', 'Leonardo S.p.A.', 'IT', lei=True),
            co('ev', 'Kongsberg Defence & Aerospace AS', 'NO'),
            co('sig', 'Nammo AS', 'NO', last_signal='2026-10-01'),
            co('none', 'Unknown Ltd', None),
        ]
        items = [syn('syn-1', '2026-11-20', 'Kongsberg Defence & Aerospace wins a NASAMS order')]
        q, counts = build(companies, items, TODAY)
        self.assertEqual([x['company_id'] for x in q], ['ev', 'sig', 'fi', 'lei', 'de', 'none'])
        self.assertIn('syn-1', q[0]['reason'])
        self.assertIn('route: NO', q[0]['reason'])
        self.assertEqual(counts, {'event': 2, 'open_register': 1, 'lei': 1, 'no_route': 1, 'no_country': 1})
        self.assertEqual([x['position'] for x in q], [1, 2, 3, 4, 5, 6])

    def test_old_or_unapproved_events_ignored(self):
        companies = [co('a', 'Kongsberg Defence & Aerospace AS', 'NO')]
        items = [syn('old', '2026-10-01', 'Kongsberg Defence & Aerospace'),
                 syn('draft', '2026-11-20', 'Kongsberg Defence & Aerospace', status='draft')]
        q, _ = build(companies, items, TODAY)
        self.assertEqual(q[0]['tier'], 'open_register')

    def test_old_signal_ignored(self):
        q, _ = build([co('a', 'Nammo AS', 'NO', last_signal='2026-01-01')], [], TODAY)
        self.assertEqual(q[0]['tier'], 'open_register')

    def test_card_skipped_unless_event(self):
        companies = [co('done', 'Patria Oyj', 'FI', has_card=True),
                     co('again', 'Crosshill Oy', 'FI', has_card=True)]
        q, _ = build(companies, [syn('s', '2026-11-19', 'Crosshill delivers sensors')], TODAY)
        self.assertEqual([(x['company_id'], x['refresh']) for x in q], [('again', True)])

    def test_card_due_for_refresh_is_kept(self):  # worker 25.11.2026: the schedule chose it, no event needed
        companies = [co('done', 'Patria Oyj', 'FI', has_card=True), co('other', 'Nammo AS', 'NO', has_card=True)]
        q, _ = build(companies, [], TODAY, keep={'done'})
        self.assertEqual([(x['company_id'], x['refresh'], x['tier']) for x in q], [('done', True, 'open_register')])

    def test_partial_word_is_not_a_mention(self):
        q, _ = build([co('a', 'Patria Oyj', 'FI')], [syn('s', '2026-11-20', 'Patriarch visits Helsinki')], TODAY)
        self.assertEqual(q[0]['tier'], 'open_register')

    def test_newest_event_first(self):
        companies = [co('a', 'Nammo AS', 'NO', last_signal='2026-09-01'),
                     co('b', 'Kitron ASA', 'NO', last_signal='2026-11-01')]
        q, _ = build(companies, [], TODAY)
        self.assertEqual([x['company_id'] for x in q], ['b', 'a'])

    def test_common_words_in_running_text_are_not_mentions(self):
        companies = [co('i', 'IMPACT', 'FR'), co('p', 'PLUG & PLAY, LLC', 'US'), co('m', 'Metric', None)]
        items = [syn('s', '2026-11-20', 'The impact of plug-and-play kits on metric targets')]
        q, _ = build(companies, items, TODAY)
        self.assertEqual({x['tier'] for x in q}, {'open_register', 'no_route', 'no_country'})

    def test_upper_case_name_matches_title_case_text(self):
        q, _ = build([co('n', 'NAVAL GROUP', 'FR')], [syn('s', '2026-11-20', 'Naval Group signs with Greece')], TODAY)
        self.assertEqual(q[0]['tier'], 'event')

    def test_one_word_name_that_is_an_ordinary_word(self):
        items = [syn('s', '2026-11-20', 'Ukraine Defense Contact Group meets; they keep in contact weekly')]
        q, _ = build([co('c', 'Contact Corporation', None), co('r', 'Rheinmetall AG', 'DE')],
                     items + [syn('t', '2026-11-20', 'Rheinmetall opens a plant')], TODAY)
        self.assertEqual({x['company_id']: x['tier'] for x in q}, {'r': 'event', 'c': 'no_country'})


if __name__ == '__main__':
    unittest.main()
