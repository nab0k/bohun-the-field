"""Website reading and product extraction (B4CE-04, slice 3.3). No network, no model."""
import io
import json
import unittest
import urllib.error

from collectors.companies import products as P
from collectors.companies.level import classify
from collectors.companies.sources import website as W

HOME = """<html><head><style>.x{}</style><script>var a=1</script></head><body>
<nav><a href="/products/">Products</a> <a href="/kariera">Kariéra</a> <a href="https://other.example/p">Other</a>
<a href="/contact">Contact</a> <a href="/solutions/radar">Radar systems</a></nav>
<p>ACME a.s. is a Czech engineering company founded in 1995. We design and build equipment for industry
and for armed forces across Europe, and we support our customers with service, training and spare parts.</p>
</body></html>"""
PRODUCTS = """<html><body><h1>Products</h1>
<p>The ACME Falcon 3D radar is a short-range air defence radar for the army, mounted on a tactical vehicle.</p>
<p>The ACME Mast is a telescopic mast for broadcasting towers and mobile networks.</p>
<p>We also offer field service for all our systems, including repairs, upgrades and operator training.</p></body></html>"""


class Resp(io.BytesIO):
    def __init__(self, body, url, ctype='text/html; charset=utf-8'):
        super().__init__(body.encode())
        self.url, self.headers = url, {'Content-Type': ctype}


class Site:
    def __init__(self, pages, robots='', fail=()):
        self.pages, self.robots, self.fail, self.asked = pages, robots, fail, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.asked.append(url)
        if url.endswith('/robots.txt'):
            return Resp(self.robots, url, 'text/plain')
        if url in self.fail:
            raise urllib.error.HTTPError(url, 403, 'Forbidden', {}, None)
        if url in self.pages:
            return Resp(self.pages[url], url)
        raise urllib.error.URLError('no route')


class TestWebsite(unittest.TestCase):
    def test_text_and_links(self):
        self.assertNotIn('var a', W.to_text(HOME))
        links = W.product_links(HOME, 'https://acme.example/')
        self.assertEqual(links, ['https://acme.example/products/', 'https://acme.example/solutions/radar'])

    def test_read_site(self):
        site = Site({'https://acme.example/': HOME, 'https://acme.example/products/': PRODUCTS},
                    fail=('https://acme.example/solutions/radar',))
        out = W.read_site('acme.example', opener=site)
        self.assertEqual([p['url'] for p in out['pages']], ['https://acme.example/', 'https://acme.example/products/'])
        self.assertIn('HTTPError', out['skipped'][0][1])  # the 403 is recorded, not worked around

    def test_robots_disallow(self):
        site = Site({'https://acme.example/': HOME}, robots='User-agent: *\nDisallow: /')
        out = W.read_site('https://acme.example/', opener=site)
        self.assertEqual(out['pages'], [])
        self.assertEqual(out['skipped'][0][1], 'robots.txt disallows')

    def test_javascript_only_page(self):
        out = W.read_site('https://acme.example/', opener=Site({'https://acme.example/': '<div id="app"></div>'}))
        self.assertIn('JavaScript', out['skipped'][-1][1])


PAGES = [{'url': 'https://acme.example/products/', 'text': W.to_text(PRODUCTS)}]


class TestVerify(unittest.TestCase):
    def test_keeps_verbatim_and_marks_military_only_with_wording(self):
        items = [
            {'name': 'ACME Falcon 3D radar', 'kind': 'product', 'specialized': True, 'url': 'https://acme.example/products',
             'quote': 'The ACME Falcon 3D radar is a short-range air defence radar for the army, mounted on a tactical vehicle.'},
            {'name': 'ACME Mast', 'kind': 'product', 'specialized': True, 'url': 'https://acme.example/products/',
             'quote': 'The ACME Mast is a telescopic mast for broadcasting towers and mobile networks.'},
        ]
        kept, rejected = P.verify(items, PAGES)
        self.assertEqual([(k['name'], k['specialized']) for k in kept], [('ACME Falcon 3D radar', True), ('ACME Mast', False)])
        self.assertEqual(rejected, [])

    def test_rejects_invented_quote_wrong_page_and_missing_name(self):
        items = [
            {'name': 'ACME Hawk', 'kind': 'product', 'specialized': True, 'url': 'https://acme.example/products/',
             'quote': 'The ACME Hawk is a loitering munition for the army.'},
            {'name': 'ACME Mast', 'kind': 'product', 'url': 'https://acme.example/news/',
             'quote': 'The ACME Mast is a telescopic mast for broadcasting towers and mobile networks.'},
            {'name': 'Falcon Mk2', 'kind': 'product', 'url': 'https://acme.example/products/',
             'quote': 'The ACME Falcon 3D radar is a short-range air defence radar for the army'},
            {'name': 'Service', 'kind': 'gadget', 'url': 'https://acme.example/products/', 'quote': 'x'},
        ]
        kept, rejected = P.verify(items, PAGES)
        self.assertEqual(kept, [])
        self.assertEqual([r['reason'] for r in rejected], [
            'quote not found verbatim on the page', 'quote page is not one of the pages read',
            'product name not inside the quote', 'missing name, quote or kind'])

    def test_trial_false_positives(self):
        """Trial 21.11.2026: self-defence cartridges and dual-use drones are not military-specific."""
        pages = [{'url': 'https://x.example/', 'text': 'XRG Defense cartridges for self-defense. Anti Drone ammunition. '
                  'MiYa se vyvíjí pro civilní i vojenské využití. L-159 ALCA Pokročilý cvičný a bojový letoun'}]
        items = [{'name': 'XRG Defense cartridges', 'kind': 'product', 'specialized': True, 'url': 'https://x.example/',
                  'quote': 'XRG Defense cartridges for self-defense.'},
                 {'name': 'Anti Drone ammunition', 'kind': 'product', 'specialized': True, 'url': 'https://x.example/',
                  'quote': 'Anti Drone ammunition.'},
                 {'name': 'MiYa', 'kind': 'product', 'specialized': True, 'url': 'https://x.example/',
                  'quote': 'MiYa se vyvíjí pro civilní i vojenské využití.'},
                 {'name': 'L-159 ALCA', 'kind': 'product', 'specialized': True, 'url': 'https://x.example/',
                  'quote': 'L-159 ALCA Pokročilý cvičný a bojový letoun'}]
        kept, _ = P.verify(items, pages)
        self.assertEqual([k['specialized'] for k in kept], [False, False, False, True])

    def test_trial_false_negatives(self):
        """Batch 1, 21.11.2026: clearly military items that the narrow rule missed."""
        quotes = ['Patria offers defence solutions, e.g. Patria NEMO turreted mortar system',
                  'The AKERON RCH170 is a loitering munition designed for rapid deployment',
                  'X-Lock VSHORAD INTERCEPTOR DISCOVER THE PRODUCT',
                  'Anti Drone ammunition', 'XRG Defense cartridges for self-defense solutions']
        self.assertEqual([bool(P.MILITARY.search(q)) for q in quotes], [True, True, True, False, False])

    def test_parse_tolerates_fences(self):
        self.assertEqual(P.parse('```json\n{"items": [{"name": "A"}]}\n```'), [{'name': 'A'}])
        self.assertEqual(P.parse('no json here'), [])


class TestLevel(unittest.TestCase):
    def test_named_military_product_gives_l1(self):
        kept, _ = P.verify([{'name': 'ACME Falcon 3D radar', 'kind': 'product', 'specialized': True,
                             'url': 'https://acme.example/products/',
                             'quote': 'The ACME Falcon 3D radar is a short-range air defence radar for the army'}], PAGES)
        r = classify(P.to_claims(kept))
        self.assertEqual((r['level'], r['rule']), ('L1', 'R01'))

    def test_civil_product_gives_no_level(self):
        kept, _ = P.verify([{'name': 'ACME Mast', 'kind': 'product', 'specialized': False,
                             'url': 'https://acme.example/products/',
                             'quote': 'The ACME Mast is a telescopic mast for broadcasting towers and mobile networks.'}], PAGES)
        self.assertEqual(classify(P.to_claims(kept))['level'], 'L0')


class TestAsk(unittest.TestCase):
    def test_openai_compatible_call(self):
        sent = {}

        def opener(req, timeout=None):
            sent['body'] = json.loads(req.data)
            sent['auth'] = req.get_header('Authorization')
            return io.BytesIO(json.dumps({'choices': [{'message': {'content': '{"items": []}'}}]}).encode())

        out = P.extract({'name': 'ACME', 'country': 'CZ'}, PAGES, ('groq', 'https://x', 'k', 'm'), opener)
        self.assertEqual(out['kept'], [])
        self.assertEqual(sent['auth'], 'Bearer k')
        self.assertIn('ACME Falcon', sent['body']['messages'][0]['content'])

    def test_no_pages_no_call(self):
        self.assertIsNone(P.extract({'name': 'A'}, [], ('groq', 'u', 'k', 'm'), opener=None)['provider'])


if __name__ == '__main__':
    unittest.main()
