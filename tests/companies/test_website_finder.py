import io
import json
import unittest

from collectors.companies import website_finder as W


class Resp(io.BytesIO):
    def __init__(self, body, url, ctype):
        super().__init__(body.encode())
        self.url, self.headers = url, {'Content-Type': ctype}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(pages):
    def op(req, timeout=None):
        url = req.full_url
        for key, (body, ctype) in pages.items():
            if url.startswith(key):
                return Resp(body, url, ctype)
        raise OSError('no route ' + url)
    return op


HTML = 'text/html'
FILL = ' lorem' * 60


class Proof(unittest.TestCase):
    c = {'name': 'LYNRED', 'legal_name': 'LYNRED', 'country': 'FR', 'city': 'VEUREY-VOROIZE', 'ids': {}}

    def test_register_number_on_the_site_is_proof(self):
        self.assertIn('334835709', W.proof(self.c, 'SAS au capital, RCS Grenoble 334 835 709', ['334835709'], False))

    def test_name_alone_is_not_proof_for_a_search_result(self):
        self.assertIsNone(W.proof(self.c, 'Lynred infrared detectors', [], False))

    def test_name_and_city_is_proof(self):
        self.assertIn('city', W.proof(self.c, 'Lynred, avenue X, 38113 Veurey-Voroize', [], False))

    def test_name_is_enough_for_a_register_site(self):
        self.assertIsNotNone(W.proof(self.c, 'Lynred infrared detectors', [], True))

    def test_legal_form_and_letters_fold(self):
        self.assertEqual(W.core('ANDØYA SPACE DEFENCE AS'), 'andoya space defence')
        self.assertEqual(W.core('Diehl Defence GmbH & Co. KG'), 'diehl defence')

    def test_directories_are_never_the_own_site(self):
        self.assertEqual(W.candidates([{'url': 'https://www.linkedin.com/company/lynred'},
                                       {'url': 'https://www.societe.com/x'}, {'url': 'https://sam.gov/e'},
                                       {'url': 'https://www.lynred.com/en/products'}]), ['https://www.lynred.com/'])


class Domain(unittest.TestCase):
    c = {'name': 'GLOBAL MILITARY PRODUCTS INC', 'legal_name': 'Global Military Products, Inc.', 'country': 'US',
         'city': 'Wilmington', 'ids': {}}

    def test_domain_that_is_the_name_with_the_name_on_the_site(self):
        # US register cities are often the Delaware agent's, so the city proof fails for a real site
        self.assertIn('domain', W.proof(self.c, 'Global Military Products, Tampa FL', [], False,
                                        'https://globalmilitaryproducts.com/'))

    def test_parent_domain_is_not_the_name(self):
        c = {'name': 'KONGSBERG DISCOVERY AS', 'country': 'NO', 'city': 'HORTEN', 'ids': {}}
        self.assertIsNone(W.proof(c, 'Kongsberg Discovery sonar', [], False, 'https://www.kongsberg.com/'))

    def test_domain_with_a_name_word_goes_first(self):
        got = W.candidates([{'url': 'https://www.globaldata.com/x'}, {'url': 'https://example.org/'},
                            {'url': 'https://globalmilitaryproducts.com/'}], self.c)
        self.assertEqual(got[0], 'https://globalmilitaryproducts.com/')


class Find(unittest.TestCase):
    def test_search_site_needs_proof_and_wrong_namesake_is_left(self):
        c = {'name': 'KAIRNTECH', 'legal_name': 'KAIRNTECH', 'country': 'FR', 'city': 'MEYLAN', 'ids': {}}
        pages = {
            'https://recherche-entreprises': (json.dumps({'results': [{'nom_complet': 'KAIRNTECH', 'siren': '844751636',
                                                                       'siege': {'libelle_commune': 'MEYLAN'}}]}), 'application/json'),
            'https://kairn.example/robots.txt': ('', 'text/plain'),
            'https://kairn.example/': ('<p>Kairntech other firm, Lyon' + FILL + '</p>', HTML),
            'https://kairntech.com/robots.txt': ('', 'text/plain'),
            'https://kairntech.com/mentions': ('<p>SIREN 844 751 636' + FILL + '</p>', HTML),
            'https://kairntech.com/': ('<a href="/mentions-legales">Mentions</a><p>Kairntech' + FILL + '</p>', HTML),
        }
        r = W.find(c, search=lambda q: [{'url': 'https://kairn.example/'}, {'url': 'https://kairntech.com/'}],
                   opener=opener(pages))
        self.assertEqual(r['website'], 'https://kairntech.com/')
        self.assertIn('844751636', r['proof'])
        alone = W.find(c, search=lambda q: [{'url': 'https://kairn.example/'}], opener=opener(pages))
        self.assertIsNone(alone['website'])
        self.assertEqual(alone['tried'][0]['result'], 'no proof on the site')

    def test_failures_never_raise(self):
        c = {'name': 'X GmbH', 'country': 'DE', 'city': 'Kiel', 'ids': {'LEI': 'ABC'}}
        r = W.find(c, search=lambda q: (_ for _ in ()).throw(OSError('down')), opener=opener({}))
        self.assertIsNone(r['website'])
        self.assertIn('search failed', r['tried'][-1]['result'])


if __name__ == '__main__':
    unittest.main()
