import unittest

from collectors.companies import discovery as X

COMPANIES = [
    {'id': 'c1', 'name': 'KONGSBERG', 'aliases': []},
    {'id': 'c2', 'name': 'Saab AB', 'aliases': []},
    {'id': 'c3', 'name': 'SAAB AKTIEBOLAG', 'aliases': ['Saab']},
    {'id': 'c4', 'name': 'General Atomics Aeronautical Systems, Inc.', 'aliases': ['GA-ASI']},
    {'id': 'c5', 'name': 'BABCOCK INTERNATIONAL GROUP PLC', 'aliases': []},
    {'id': 'c6', 'name': 'Thales Alenia Space', 'aliases': []},
]


class Parts(unittest.TestCase):
    def test_split_and_aliases(self):
        self.assertEqual(X.parts('Rolls-Royce / MTU'), [('Rolls-Royce', None), ('MTU', None)])
        self.assertEqual(X.parts('Rheinmetall BAE Systems Land (RBSL) and KNDS UK (WFEL)'),
                         [('Rheinmetall BAE Systems Land', 'RBSL'), ('KNDS UK', 'WFEL')])
        self.assertEqual(X.parts('PT PAL Indonesia (Surabaya)'), [('PT PAL Indonesia', None)])
        self.assertEqual(X.parts('BAE Systems Bofors AB, Karlskoga, Sweden'), [('BAE Systems Bofors AB', None)])
        self.assertEqual(X.parts('Wärtsilä Corporation – Helsinki'), [('Wärtsilä Corporation', None)])


class Classify(unittest.TestCase):
    def cat(self, name):
        return X.classify(name)[0]

    def test_companies(self):
        for name in ['Echodyne Corp', 'Porr AG', 'Anduril Industries', 'Huntington Ingalls Industries',
                     'Khosla Ventures', 'Mitsubishi Heavy Industries', 'Elbit Systems Deutschland GmbH']:
            self.assertEqual(self.cat(name), 'company', name)

    def test_not_companies(self):
        for name in ['THAAD interceptor', 'Sentinel AN/MPQ-64', 'Powertrain', 'D5 missile', 'Control Systems',
                     'Additive Manufacturing Powder', 'Rocket Motor', 'Italie', 'Unknown',
                     'Russian defense manufacturers', 'Aster 15/30 Air Defense Missiles',
                     'Field sensors, soil moisture meters, and drones used for spatial vegetation mapping',
                     'BAE Systems AGM-114 Hellfire', 'Navigation Systems']:
            self.assertNotEqual(self.cat(name), 'company', name)

    def test_public_bodies_and_universities(self):
        for name in ['U.S. Navy', 'British Army', 'Defense Department', 'Swedish Navy', 'OTAN',
                     'Israel Missile Defense Organization', 'ukrainisches Verteidigungsministerium']:
            self.assertEqual(self.cat(name), 'gov', name)
        self.assertEqual(self.cat('Univerzita Karlova'), 'university')
        self.assertNotEqual(self.cat('Military Harness'), 'gov')


class Match(unittest.TestCase):
    def test_one_many_none(self):
        ix = X.Index(COMPANIES)
        self.assertEqual(ix.match('Kongsberg'), ('linked', ['c1']))
        self.assertEqual(ix.match('Saab'), ('ambiguous', ['c2', 'c3']))  # two records in the base: no link
        self.assertEqual(ix.match('General Atomics', 'GA-ASI'), ('linked', ['c4']))
        self.assertEqual(ix.match('Anduril Industries'), ('new', []))
        self.assertEqual(ix.match('Babcock International'), ('linked_core', ['c5']))  # the same name up to Group plc
        self.assertEqual(ix.match('Babcock'), ('new', []))  # one short word is not enough
        self.assertEqual(ix.match('Thales'), ('new', []))
        spell = X.Index([{'id': 'f', 'name': 'Fairbanks Morse Defense'}, {'id': 'k', 'name': 'Creotech Instruments S.A.'}])
        self.assertEqual(spell.match('Fairbanks Morse Defence'), ('linked', ['f']))
        self.assertEqual(spell.match('Creotech Instruments S.A'), ('linked', ['k']))
        self.assertEqual(X.Index([{'id': 'h', 'name': 'Honeywell International s.r.o.'}]).match('Honeywell'), ('new', []))
        self.assertEqual(X.Index([{'id': 'w', 'name': 'The Williams Group'}]).match('Williams International'),
                         ('new', []))

    def test_plan_and_summary(self):
        mentions = [{'id': 1, 'kind': 'company', 'name': 'Kongsberg / Anduril Industries', 'items': 2},
                    {'id': 2, 'kind': 'company', 'name': 'THAAD interceptor', 'items': 1},
                    {'id': 3, 'kind': 'org', 'name': 'Swedish Navy', 'items': 1}]
        dec = X.plan(mentions, COMPANIES)
        self.assertEqual([(d['name'], d['category'], d['match']) for d in dec],
                         [('Kongsberg', 'company', 'linked'), ('Anduril Industries', 'company', 'new'),
                          ('THAAD interceptor', 'not_org', ''), ('Swedish Navy', 'gov', '')])
        s = X.summary(dec)
        self.assertEqual((s['mentions'], s['parts'], s['new_companies_distinct']), (3, 4, 1))


class Model(unittest.TestCase):
    def test_parse_and_classify(self):
        self.assertEqual(X.parse('noise [{"i": 1, "c": "company", "country": "tr"}, {"i": 2, "c": "x"}, {"i": 9, "c": "gov"}]', 2),
                         {1: ('company', 'TR')})
        self.assertEqual(X.parse('no json', 3), {})
        dec = [{'node_id': 1, 'name': 'Aselsan', 'category': 'unsure', 'reason': 'r', 'match': 'new', 'company_ids': []},
               {'node_id': 2, 'name': 'John Smith', 'category': 'unsure', 'reason': 'r', 'match': 'new', 'company_ids': []},
               {'node_id': 3, 'name': 'Thing', 'category': 'unsure', 'reason': 'r', 'match': 'new', 'company_ids': []},
               {'node_id': 4, 'name': 'Kongsberg', 'category': 'company', 'reason': 'r', 'match': 'linked', 'company_ids': ['c1']}]
        seen = []

        def ask(text):
            seen.append(text)
            return '[{"i": 1, "c": "company", "country": "TR"}, {"i": 2, "c": "person"}]'
        stats = X.classify_unsure(dec, ask, {1: 'Aselsan builds radars'})
        self.assertEqual(stats, {'asked': 3, 'answered': 2, 'errors': 0})
        self.assertIn('Aselsan builds radars', seen[0])
        self.assertNotIn('Kongsberg', seen[0])  # only 'unsure' names go to the model
        self.assertEqual([(d['category'], d['reason']) for d in dec[:3]],
                         [('company', 'model'), ('not_org', 'model: person'), ('unsure', 'r')])
        self.assertEqual(dec[0]['model_country'], 'TR')

    def test_failed_batch_keeps_unsure(self):
        dec = [{'node_id': 1, 'name': 'A', 'category': 'unsure', 'reason': 'r', 'match': 'new', 'company_ids': []}]

        def ask(text):
            raise OSError('HTTP 402')
        self.assertEqual(X.classify_unsure(dec, ask, log=lambda m: None), {'asked': 1, 'answered': 0, 'errors': 1})
        self.assertEqual(dec[0]['category'], 'unsure')


if __name__ == '__main__':
    unittest.main()
