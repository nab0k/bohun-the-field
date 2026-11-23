import base64
import gzip
import importlib.util
import json
import unittest
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location('export', Path(__file__).resolve().parents[2] / 'scripts/company-cards-export.py')
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)


def unpack(text):
    return json.loads(gzip.decompress(base64.b64decode(text)))


class Pack(unittest.TestCase):
    def test_recent_newest_first(self):
        cards = [{'company_id': 'a', 'reviewed': '2026-11-10'}, {'company_id': 'b', 'reviewed': '2026-11-22'},
                 {'company_id': 'c', 'reviewed': '2026-11-23'}]
        text, sent, cat, left = E.pack(cards, [{'name': 'Rocket Lab', 'country': 'US', 'added': '2026-11-23'}],
                                       days=7, today=date(2026, 11, 24))
        got = unpack(text)
        self.assertEqual(([c['company_id'] for c in got['cards']], got['catalogue'], sent, cat, left),
                         (['c', 'b'], [{'name': 'Rocket Lab', 'country': 'US'}], 2, 1, 0))

    def test_cut_to_the_limit(self):
        import random
        rnd = random.Random(1)
        cards = [{'company_id': str(i), 'reviewed': '2026-11-23',
                  'blob': ''.join(rnd.choice('abcdefghij') for _ in range(3000))} for i in range(40)]
        cat = [{'name': f'Company {i}', 'country': ''} for i in range(500)]
        text, sent, sent_cat, left = E.pack(cards, cat, today=date(2026, 11, 24), limit=20000)
        self.assertLessEqual(len(text), 20000)
        self.assertEqual((sent_cat, sent + left), (0, 540 - 0))  # the catalogue is cut first
        self.assertGreater(left, 500)
        self.assertEqual(len(unpack(text)['cards']), sent)


class Chunks(unittest.TestCase):
    def test_parts_under_the_limit_catalogue_first(self):
        import random
        rnd = random.Random(2)
        cards = [{'company_id': str(i), 'updated': '2026-11-23',
                  'blob': ''.join(rnd.choice('abcdefghij') for _ in range(3000))} for i in range(30)]
        cat = [{'name': f'Company {i}', 'country': ''} for i in range(50)]
        parts = E.chunks(cards, cat, limit=20000)
        self.assertTrue(all(len(p) <= 20000 for p in parts))
        got = [unpack(p) for p in parts]
        self.assertEqual(sum(len(g['catalogue']) for g in got), 50)
        self.assertEqual(sum(len(g['cards']) for g in got), 30)
        self.assertTrue(got[0]['catalogue'] and not got[0]['cards'])


if __name__ == '__main__':
    unittest.main()
