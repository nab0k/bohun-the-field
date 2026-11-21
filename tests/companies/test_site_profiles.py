"""Round trip: site profile file -> profile_* rows -> rebuilt profiles (B5CC-01)."""
import json
import os
import unittest

from collectors.companies.site_profiles import from_rows, insert_sql, to_rows

SAMPLE = {
    'country': 'DE', 'reviewed': '2026-11-20', 'status_label': 'Reviewed card',
    'about': {'workspace': "O'Neil text", 'overview': 'x'},
    'evidence': [
        {'claim': 'Legal entity', 'status': 'Verified', 'source': 'GLEIF', 'href': 'https://api.gleif.org/x'},
        {'claim': 'Site', 'status': 'Supported', 'source': 'Company website', 'href': 'http://insecure.example'},
        {'claim': 'Dup', 'status': 'Supported', 'source': 'GLEIF', 'href': 'https://api.gleif.org/x'},
    ],
    'productTree': [],
}


class TestRoundTrip(unittest.TestCase):
    def test_sample(self):
        sources, claims, links = to_rows('acme-ag', SAMPLE, 'co_1', 100, 500, '2026-11-20', 'note')
        self.assertEqual([s['source_url'] for s in sources], ['https://api.gleif.org/x'])  # http and duplicate skipped
        self.assertEqual(sources[0]['source_kind'], 'legal_entity_identifier_reference')
        self.assertTrue(all(c['qa_status'] == 'APPROVED' and c['public_release'] is False for c in claims))
        self.assertTrue(all(c['field_path'].startswith('/site_profile/') for c in claims))
        self.assertEqual(len(links), len(claims))
        self.assertEqual(from_rows(claims), {'acme-ag': SAMPLE})
        sql = insert_sql(sources, claims, links)
        self.assertTrue(sql.startswith('begin;') and sql.endswith('commit;'))
        self.assertIn("O''Neil", sql)

    def test_hold_rows_are_not_built(self):
        _, claims, _ = to_rows('acme-ag', SAMPLE, 'co_1', 1, 1, '2026-11-20', 'n')
        for c in claims:
            c['qa_status'] = 'HOLD'
        self.assertEqual(from_rows(claims), {})

    def test_live_file_if_present(self):
        path = os.environ.get('SITE_PROFILES_JSON')
        if not path:
            self.skipTest('SITE_PROFILES_JSON not set')
        data = json.load(open(path, encoding='utf-8'))
        rows, sid, cid = [], 1, 1
        for slug, prof in data['profiles'].items():
            s, c, _ = to_rows(slug, prof, 'co_' + slug, sid, cid, '2026-11-20', 'n')
            sid += len(s); cid += len(c); rows += c
        self.assertEqual(from_rows(rows), data['profiles'])


if __name__ == '__main__':
    unittest.main()
