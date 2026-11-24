import unittest

from collectors.companies import graph as G


def row(rel, outgoing, kind, name, status='verified', **kw):
    return dict({'rel': rel, 'outgoing': outgoing, 'kind': kind, 'name': name, 'status': status, 'occurred_on': None,
                 'created_at': '2026-11-20', 'amount': None, 'currency': None, 'quote': 'q', 'source_url': 'u-' + name,
                 'other_company': None, 'other_country': None}, **kw)


class Network(unittest.TestCase):
    def test_types_by_direction(self):
        self.assertEqual(G.kind_of('orders', False, 'org'), 'customer')     # the navy orders from the company
        self.assertEqual(G.kind_of('orders', True, 'company'), 'supplier')   # the company orders from Saab
        self.assertEqual(G.kind_of('supplies', False, 'company'), 'supplier')
        self.assertEqual(G.kind_of('supplies', True, 'org'), 'customer')
        self.assertEqual(G.kind_of('funds', False, 'org'), 'funder')
        self.assertEqual(G.kind_of('funds', False, 'company'), 'investor')
        self.assertEqual(G.kind_of('part_of', True, 'company'), 'owner')
        self.assertEqual(G.kind_of('part_of', False, 'company'), 'subsidiary')
        self.assertIsNone(G.kind_of('orders', False, 'technology'))       # noise: not a company or organisation

    def test_merge_dedupe_and_products(self):
        rows = [row('orders', False, 'org', 'French Armaments General Directorate (DGA)'),
                row('orders', False, 'org', 'French Directorate General of Armaments (DGA)', occurred_on='2026-11-03'),
                row('orders', True, 'company', 'Saab', amount=180e6, currency='EUR'),
                row('supplies', False, 'company', 'Saab'),
                row('supplies', False, 'company', 'METLEN', status='candidate', other_company='METLEN', other_country='GR'),
                row('competes', True, 'company', 'TKMS', status='candidate'),
                row('supplies', False, 'technology', 'Autonomous Navigation Software'),
                row('produces', True, 'product', 'Rampart'),
                row('supplies', True, 'programme', 'Orka-class'),
                row('orders', False, 'company', 'NAVAL GROUP SA')]
        net, prods = G.build(rows, ['Naval Group'])
        names = {(n['type'], n['name']) for n in net}
        self.assertIn(('customer', 'French Directorate General of Armaments (DGA)'), names)
        self.assertEqual(sum(1 for n in net if n['type'] == 'customer'), 1)  # one DGA
        saab = [n for n in net if n['name'] == 'Saab']
        self.assertEqual(len(saab), 1)  # ordered from it and it supplies: one supplier node
        self.assertEqual((saab[0]['count'], saab[0]['amount']), (2, 180e6))
        metlen = next(n for n in net if n['name'] == 'METLEN')
        self.assertEqual((metlen['status'], metlen['in_catalogue'], metlen['country']), ('candidate', True, 'GR'))
        self.assertIn(('competitor', 'TKMS'), names)
        self.assertNotIn('Autonomous Navigation Software', {n['name'] for n in net})
        self.assertNotIn('NAVAL GROUP SA', {n['name'] for n in net})  # itself
        self.assertEqual([(p['name'], p['role']) for p in prods], [('Rampart', 'makes'), ('Orka-class', 'supplies to')])
        self.assertTrue(all(n['evidence'][0]['quote'] for n in net))


if __name__ == '__main__':
    unittest.main()
