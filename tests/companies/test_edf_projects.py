import io
import json
import unittest

from collectors.companies.sources import edf_projects as E

MD = {'projectId': ['101302020'], 'language': ['en'], 'acronym': ['MINERVA'],
      'title': ['Modular Integration of Naval\nEnergy Systems'], 'topicAbbreviation': ['EDF-2025-LS-RA-SI-X'],
      'callIdentifier': ['EDF-2025-LS-RA-SI'], 'status': ['Ongoing'], 'startDate': ['2026-10-01T00:00:00.000+0100'],
      'endDate': ['2029-09-30T00:00:00.000+0100'], 'euContributionAmount': ['19734908.31'],
      'participants': [json.dumps([{'role': 'coordinator', 'legalName': 'NVL BV & CO KG', 'pic': '1'},
                                   {'role': 'participant', 'legalName': 'NAVAL GROUP', 'pic': '906', 'eucontribution': '1511076.62'}])]}


class Details(unittest.TestCase):
    def test_parse_and_company(self):
        d = E.parse(MD)
        self.assertEqual((d['acronym'], d['title'], d['partners'], d['coordinator'], d['eu_total'], d['start']),
                         ('MINERVA', 'Modular Integration of Naval Energy Systems', 2, 'NVL BV & CO KG', 19734908.31, '2026-10-01'))
        me = E.for_company(d, pics=['906'])
        self.assertEqual((me['role'], me['company_eu']), ('participant', 1511076.62))
        self.assertNotIn('_participants', me)
        self.assertEqual(E.for_company(d, names=['Naval Group'])['role'], 'participant')  # by name when no PIC
        self.assertNotIn('role', E.for_company(d, names=['Somebody Else']))

    def test_other_participants_short_form(self):  # EXP-035: the network, never addresses
        md = dict(MD, participants=[json.dumps([
            {'role': 'coordinator', 'legalName': 'NAVAL  GROUP BELGIUM', 'pic': 893356906, 'type': 'PRC',
             'postalAddress': {'street': '15 BOULEVARD', 'city': 'BRUSSELS', 'countryCode': {'abbreviation': 'BE'}},
             'latitude': '50.85', 'webLink': 'x'},
            {'role': 'participant', 'legalName': 'TNO', 'pic': '7', 'type': 'REC'},
            {'role': 'participant', 'legalName': 'NAVAL GROUP', 'pic': '906'}])])
        me = E.for_company(E.parse(md), pics=['906'])
        self.assertEqual(me['participants'], [
            {'name': 'NAVAL GROUP BELGIUM', 'country': 'BE', 'kind': 'company', 'role': 'coordinator', 'pic': '893356906'},
            {'name': 'TNO', 'country': None, 'kind': 'research', 'role': 'participant', 'pic': '7'}])
        self.assertNotIn('BRUSSELS', json.dumps(me))

    def test_fetch_batches(self):
        seen = []

        def opener(req, timeout=None):
            seen.append(req.full_url)
            return io.BytesIO(json.dumps({'results': [{'metadata': MD}]}).encode())
        got = E.fetch(['101302020'], opener)
        self.assertEqual(list(got), ['101302020'])
        self.assertIn('SEDIA_NONH2020_PROD', seen[0])


if __name__ == '__main__':
    unittest.main()
