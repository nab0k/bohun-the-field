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
        text, sent, dropped = E.pack(cards, days=7, today=date(2026, 11, 24))
        self.assertEqual(([c['company_id'] for c in unpack(text)], sent, dropped), (['c', 'b'], 2, 0))

    def test_cut_to_the_limit(self):
        import random
        rnd = random.Random(1)
        cards = [{'company_id': str(i), 'reviewed': '2026-11-23',
                  'blob': ''.join(rnd.choice('abcdefghij') for _ in range(3000))} for i in range(40)]
        text, sent, dropped = E.pack(cards, today=date(2026, 11, 24), limit=20000)
        self.assertLessEqual(len(text), 20000)
        self.assertEqual(sent + dropped, 40)
        self.assertGreater(dropped, 0)
        self.assertEqual(len(unpack(text)), sent)


if __name__ == '__main__':
    unittest.main()
