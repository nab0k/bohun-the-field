import unittest
from datetime import date

from collectors.companies import refresh as F

T = date(2026, 11, 23)


def row(i, card=False, level=None, route=False, last=None, event=False, size=0):
    return {'id': i, 'country': 'CZ', 'level': level, 'has_card': card, 'has_route': route, 'last_check': last, 'event': event,
            'size': size}


class Schedule(unittest.TestCase):
    def test_periods_and_order(self):
        rows = [row('l3-fresh', True, 'L3', True, '2026-11-01'),      # 22 days < 60: not due
                row('l3-old', True, 'L3', True, '2026-08-01'),        # 114 days: due, ratio 1.9
                row('l1-old', True, 'L1', True, '2026-10-01'),        # 53 days >= 30: due, ratio 1.77
                row('event', False, None, True, '2026-11-22', True),  # named in the news after its check
                row('cand', False, None, True, None),                 # never checked
                row('other-recent', False, None, False, '2026-10-01')]  # 53 < 180
        got = [r['id'] for r, t, k in F.due(rows, T)]
        self.assertEqual(got, ['event', 'l3-old', 'l1-old', 'cand'])

    def test_large_first(self):  # owner 23.11.2026: large companies first, the news in parallel and ahead
        rows = [row('small', False, None, True, None, size=1), row('big', False, None, True, None, size=40),
                row('news-small', False, None, True, '2026-11-20', True, size=2),
                row('news-big', False, None, True, '2026-11-20', True, size=30)]
        self.assertEqual([r['id'] for r, t, k in F.due(rows, T)], ['news-big', 'news-small', 'big', 'small'])

    def test_plan_split_and_budget(self):
        rows = [row('c1', True, 'L3', True, '2026-01-01'), row('k1', False, None, True, None),
                row('o1', False, None, False, None), row('c2', True, 'L4', True, '2026-02-01')]
        self.assertEqual(F.plan(rows, 10, T), (['c1', 'c2'], ['k1']))  # no-route companies wait for the route step
        self.assertEqual(F.plan(rows, 1, T), (['c1'], []))

    def test_card_without_a_route_is_left_to_its_own_step(self):  # contract cards (US UEI), 25.11.2026
        rows = [row('us', True, 'L3', False, None), row('cz', True, 'L3', True, None)]
        self.assertEqual(F.plan(rows, 10, T), (['cz'], []))

    def test_route_attempt_does_not_count_as_a_card_check(self):  # 797 companies never filled, 25.11.2026
        self.assertIn('case when has_card or has_route then last_card', F.DUE_SQL)

    def test_metric(self):
        rows = [row('a', True, 'L3', True, '2026-11-01'), row('b', True, 'L1', True, '2026-10-01'),
                row('c', False, None, False, None), row('d', False, None, True, '2026-11-23')]
        m = F.metric(rows, T)
        self.assertEqual((m['total'], m['on_time'], m['overdue'], m['never'], m['today']), (4, 2, 1, 1, 1))
        self.assertEqual(m['tiers']['L3'], {'total': 1, 'on_time': 1})


if __name__ == '__main__':
    unittest.main()
