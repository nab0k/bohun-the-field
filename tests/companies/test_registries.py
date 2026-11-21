"""National registers for the legal-identity step (B4CE-04, slice 3.1). No network: trimmed real responses."""
import io
import json
import os
import unittest

from collectors.companies.normalize import name_norm
from collectors.companies.sources import registries as R

FIX = os.path.join(os.path.dirname(__file__), 'fixtures', 'registries')


def fixture(name):
    with open(os.path.join(FIX, name), encoding='utf-8') as f:
        return json.load(f)


class Opener:
    """Serves fixtures by URL substring; records the URLs asked."""

    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.urls.append(url)
        for key, payload in self.routes.items():
            if key in url:
                if isinstance(payload, Exception):
                    raise payload
                return io.BytesIO(json.dumps(payload).encode())
        raise OSError('404 ' + url)


class TestNormalise(unittest.TestCase):
    def test_polish_forms(self):
        self.assertEqual(name_norm('PIT-RADWAR SPÓŁKA AKCYJNA'), name_norm('PIT-RADWAR S.A.'))
        self.assertEqual(name_norm('WB Electronics Spółka z ograniczoną odpowiedzialnością'), name_norm('WB Electronics sp. z o.o.'))


class TestNorway(unittest.TestCase):
    def test_match_skips_foreign_branches(self):
        op = Opener({'brreg': fixture('no_kongsberg.json')})
        r = R.resolve('Kongsberg Defence & Aerospace AS', 'NO', opener=op)
        self.assertEqual((r['decision'], r['id'], r['founded'], r['city']), ('matched', '978614582', '1997-05-28', 'KONGSBERG'))
        self.assertTrue(r['source_url'].endswith('/978614582'))
        # 'KONGSBERG DEFENCE OY' is a Finnish branch (UTLA) and must not resolve as a Norwegian entity
        self.assertEqual(R.resolve('Kongsberg Defence Oy', 'NO', opener=op)['decision'], 'not_found')

    def test_brand_does_not_resolve(self):
        op = Opener({'brreg': fixture('no_kongsberg.json')})
        self.assertEqual(R.resolve('Kongsberg Defence', 'NO', opener=op)['decision'], 'not_found')


class TestFrance(unittest.TestCase):
    def test_match_and_placeholder_date(self):
        op = Opener({'recherche-entreprises': fixture('fr_arquus.json')})
        r = R.resolve('Arquus SAS', 'FR', opener=op)
        self.assertEqual((r['decision'], r['id'], r['city']), ('matched', '662043405', 'VERSAILLES'))
        self.assertIsNone(r['founded'])  # register gives 1900-01-01, a placeholder

    def test_individual_never_matches(self):
        op = Opener({'recherche-entreprises': fixture('fr_arquus.json')})
        self.assertEqual(R.resolve('Arquus Test', 'FR', opener=op)['decision'], 'not_found')

    def test_retry_without_legal_form(self):
        asked = []
        def opener(req, timeout=None):
            asked.append(req.full_url)
            hit = 'q=GDTECH%20FRANCE&' in req.full_url
            rows = [{'siren': '9', 'nom_raison_sociale': 'GDTECH FRANCE', 'nature_juridique': '5710',
                     'etat_administratif': 'A', 'siege': {}}] if hit else []
            return io.BytesIO(json.dumps({'results': rows}).encode())
        r = R.resolve('GDTECH FRANCE SAS', 'FR', opener=opener)
        self.assertEqual((r['decision'], r['id'], len(asked)), ('matched', '9', 2))


class TestFinland(unittest.TestCase):
    def test_current_name_only(self):
        op = Opener({'avoindata.prh.fi': fixture('fi_patria.json')})
        r = R.resolve('Patria Oyj', 'FI', opener=op)
        # 1077447-2 carried the name 'Patria Oyj' until 2011 and is now Patria Finance Oyj
        self.assertEqual((r['decision'], r['id'], r['city']), ('matched', '2334159-6', 'Helsinki'))

    def test_former_name_does_not_match(self):
        op = Opener({'avoindata.prh.fi': fixture('fi_patria.json')})
        self.assertEqual(R.resolve('Suomen Puolustusväline', 'FI', opener=op)['decision'], 'not_found')


class TestPoland(unittest.TestCase):
    def test_krs_confirms_name(self):
        op = Opener({'0000297470?rejestr=P': fixture('pl_0000297470.json')})
        r = R.resolve('PIT-RADWAR S.A.', 'PL', opener=op, registered_as='297470')
        self.assertEqual((r['decision'], r['id'], r['founded'], r['city']), ('matched', '0000297470', '2008-02-07', 'Warszawa'))
        self.assertEqual(r['activity'], ['72.10Z'])

    def test_wrong_name_for_number(self):
        op = Opener({'0000004324?rejestr=P': fixture('pl_0000004324.json')})
        self.assertEqual(R.resolve('PIT-RADWAR S.A.', 'PL', opener=op, registered_as='0000004324')['decision'], 'not_found')

    def test_no_number_no_guess(self):
        r = R.resolve('PIT-RADWAR S.A.', 'PL', opener=Opener({}))
        self.assertEqual(r['decision'], 'not_found')
        self.assertIn('no name search', r['reason'])

    def test_falls_back_to_association_register(self):
        op = Opener({'0000004324?rejestr=P': OSError('404'), '0000004324?rejestr=S': fixture('pl_0000004324.json')})
        self.assertEqual(R.resolve('Huta Stalowa Wola S.A.', 'PL', opener=op, registered_as='4324')['decision'], 'matched')


class TestRouter(unittest.TestCase):
    def test_country_without_free_register(self):
        r = R.resolve('Hensoldt AG', 'DE', opener=Opener({}))
        self.assertEqual(r['decision'], 'not_found')
        self.assertIn('no free register', r['reason'])

    def test_disputed_when_two_entities_share_the_name(self):
        rows = [{'id': '1', 'legal_name': 'X AS'}, {'id': '2', 'legal_name': 'X ASA'}]
        self.assertEqual(R._decide('X AS', rows, lambda r: r['legal_name'])['decision'], 'disputed')


if __name__ == '__main__':
    unittest.main()
