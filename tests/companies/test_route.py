import unittest
from datetime import date

from collectors.companies import route as R


class FakeConn:
    def __init__(self, rows, taken=()):
        self.rows, self.taken, self.sql = rows, set(taken), []

    def execute(self, sql, params=None):
        self.sql.append((sql.split()[0], params))
        self._one = None
        if sql.startswith('insert into companies.company_identifier'):
            self._one = None if params['value'] in self.taken else {'company_id': params['company_id']}
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self._one

    def transaction(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def found(lei=None, lei_decision='not_found', qid=None, country=None):
    wd = {'decision': 'matched', 'qid': qid} if qid else {'decision': 'not_found'}
    return {'wikidata': wd, 'lei': {'decision': lei_decision, 'lei': lei} if lei else {'decision': lei_decision},
            'country': country}


class Outcome(unittest.TestCase):
    def test_outcomes(self):
        self.assertEqual(R.outcome({'country': 'DE'}, found('L1', 'matched'))[:4], ('found', 'L1', None, None))
        self.assertEqual(R.outcome({'country': None}, found(qid='Q1', country='FR'))[:4], ('found', None, 'Q1', 'FR'))
        self.assertEqual(R.outcome({'country': 'IT'}, found())[0], 'not_found')
        c = {'wikidata': {'decision': 'not_found'}, 'lei': {'decision': 'conflict', 'candidates': ['A', 'B']}}
        self.assertEqual(R.outcome({'country': 'DE'}, c)[:2], ('conflict', None))


class Run(unittest.TestCase):
    rows = [{'id': 'c1', 'name': 'Diehl Defence GmbH & Co. KG', 'country': 'DE'},
            {'id': 'c2', 'name': 'Safran', 'country': None},
            {'id': 'c3', 'name': 'Nobody GmbH', 'country': 'DE'}]
    answers = {'Diehl Defence GmbH & Co. KG': found('L1', 'matched'), 'Safran': found('L2', 'from_wikidata', 'Q2', 'FR'),
               'Nobody GmbH': found()}

    def test_dry_run_writes_nothing(self):
        conn = FakeConn(self.rows)
        s = R.run(conn, write=False, enrich=lambda n, c: self.answers[n], today=date(2026, 11, 24))
        self.assertEqual(s['outcomes'], {'found': 2, 'not_found': 1})
        self.assertEqual([k for k, _ in conn.sql], ['select'])

    def test_write_and_taken(self):
        conn = FakeConn(self.rows, taken={'L2'})
        s = R.run(conn, write=True, enrich=lambda n, c: self.answers[n], today=date(2026, 11, 24))
        self.assertEqual((s['lei'], s['qid'], s['country'], s['taken']), (2, 1, 1, 1))
        self.assertEqual(s['outcomes'], {'found': 1, 'taken': 1, 'not_found': 1})
        attempts = [p for k, p in conn.sql if k == 'insert' and 'run' in p]
        self.assertEqual([a['outcome'] for a in attempts], ['found', 'taken', 'not_found'])
        self.assertIn(('update', {'country': 'FR', 'company_id': 'c2'}), conn.sql)


if __name__ == '__main__':
    unittest.main()
