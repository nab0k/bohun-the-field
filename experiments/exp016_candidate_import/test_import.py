"""Offline: the same SQL statements on the SQLite schema of the companies store. Real PostgreSQL runs in the workflow."""
import ast
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest import mock

from collectors.companies.store import SqliteStore
from experiments.exp016_candidate_import import batch as b, run as r

AT = '2026-11-05T12:00:00+00:00'


class Conn:
    def __init__(self):
        self.db = SqliteStore(':memory:').db

    def execute(self, sql, params=()):
        return self.db.execute(sql.replace('%s', '?').replace('::jsonb', ''), params)

    def seed(self, cid='co_old_1'):
        self.db.execute('INSERT INTO company(id,name,name_norm,created_at,updated_at) VALUES(?,?,?,?,?)', (cid, cid, cid, AT, AT))
        self.db.execute('INSERT INTO company_identifier(scheme,value,company_id,source,observed_at) VALUES(?,?,?,?,?)', ('LEI', 'OLD' + cid, cid, 'x', AT))
        obs = self.db.execute("INSERT INTO observation(source,source_key,content_hash,fetched_at,payload) VALUES('x',?,'h',?,'{}') RETURNING id", (cid, AT)).fetchone()[0]
        self.db.execute("INSERT INTO signal(company_id,type,layer,observation_id) VALUES(?,'grant','A',?)", (cid, obs))


def company(n=1, **kw):
    c = {'id': f'co_c208_{n:04}', 'name': f'Rotor {n} GmbH', 'name_norm': f'rotor {n}', 'country': 'DE', 'layer': 'A', 'flags': [b.FLAG],
         'identifiers': [{'scheme': 'EU_PIC', 'value': f'9{n:08}'}], 'aliases': [{'alias': f'Rotor {n}', 'alias_norm': f'rotor {n}'}],
         'categories': [{'axis': 'capability', 'value': 'WPN', 'basis': 'CPV:35'}],
         'signals': [{'type': 'grant', 'layer': 'A', 'counterpart': 'EDF', 'date': '2024-01-02', 'url': 'https://x.test/1', 'origin': 'edf'},
                     {'type': 'grant', 'layer': 'A', 'counterpart': 'EDF', 'date': '2024-01-02', 'url': 'https://x.test/2', 'origin': 'edf'}],
         'website_observed': 'https://rotor.test/'}
    c.update(kw)
    return c


def packet(*companies):
    return {'format': 'exp016.candidate_batch.v1', 'batch': b.BATCH, 'companies': list(companies or (company(1), company(2)))}


class Batch(unittest.TestCase):
    def setUp(self):
        self.c = Conn()
        self.c.seed()

    def test_apply_counts_and_verify(self):
        before = b.precheck(self.c, b.fingerprint(self.c))
        report = b.apply(self.c, packet(), AT)
        self.assertEqual((report['companies'], report['identifiers'], report['signals'], report['observations']), (2, 2, 4, 4))
        b.verify(before, b.counts(self.c), report)

    def test_status_is_candidate_and_no_website_is_written(self):
        b.apply(self.c, packet(), AT)
        rows = self.c.execute('SELECT status,website,flags FROM company WHERE id LIKE %s', (b.ID_PREFIX + '%',)).fetchall()
        self.assertEqual({(x[0], x[1]) for x in rows}, {('candidate', None)})
        self.assertIn(b.FLAG, rows[0][2])

    def test_website_stays_in_the_observation(self):
        b.apply(self.c, packet(company(1)), AT)
        payloads = [json.loads(x[0]) for x in self.c.execute('SELECT payload FROM observation WHERE source=%s', (b.SOURCE,)).fetchall()]
        self.assertEqual(sum('website_observed' in p for p in payloads), 1)

    def test_taken_identifier_skips_the_whole_company(self):
        report = b.apply(self.c, packet(company(1, identifiers=[{'scheme': 'LEI', 'value': 'OLDco_old_1'}]), company(2)), AT)
        self.assertEqual((report['companies'], report['skipped_identifier_conflict']), (1, 1))
        self.assertEqual(self.c.execute('SELECT company_id FROM company_identifier WHERE value=%s', ('OLDco_old_1',)).fetchone()[0], 'co_old_1')

    def test_changed_registry_stops_before_writing(self):
        pin = b.fingerprint(self.c)
        self.c.seed('co_old_2')
        with self.assertRaisesRegex(b.BatchError, 'REGISTRY_CHANGED'):
            b.precheck(self.c, pin)

    def test_repeat_is_refused(self):
        b.apply(self.c, packet(), AT)
        with self.assertRaisesRegex(b.BatchError, 'BATCH_ALREADY_PRESENT'):
            b.precheck(self.c, b.fingerprint(self.c))

    def test_undo_restores_everything_and_keeps_neighbours(self):
        pin, before = b.fingerprint(self.c), b.counts(self.c)
        b.apply(self.c, packet(), AT)
        self.assertEqual(b.undo(self.c, packet()), {'removed_companies': 2, 'removed_observations': 4})
        self.assertEqual((b.fingerprint(self.c), b.counts(self.c)), (pin, before))

    def test_undo_refuses_more_than_the_batch(self):
        b.apply(self.c, packet(), AT)
        with self.assertRaisesRegex(b.BatchError, 'MORE_THAN_THE_BATCH'):
            b.undo(self.c, packet(company(1)))

    def test_transaction_rollback_leaves_nothing(self):
        before = b.counts(self.c)
        self.c.db.commit()
        b.apply(self.c, packet(), AT)
        self.c.db.rollback()
        self.assertEqual(b.counts(self.c), before)

    def test_verify_catches_a_mismatch(self):
        before = b.counts(self.c)
        report = b.apply(self.c, packet(), AT)
        report['companies'] += 1
        with self.assertRaisesRegex(b.BatchError, 'COUNT_MISMATCH'):
            b.verify(before, b.counts(self.c), report)


class Packet(unittest.TestCase):
    def test_rejects_unsafe_records(self):
        cases = [(company(website='https://x.test'), 'WEBSITE'), (company(status='card'), 'STATUS'), (company(country='UA'), 'COUNTRY'),
                 (company(country='RU'), 'COUNTRY'), (company(flags=[]), 'BATCH_FLAG'), (company(signals=[]), 'SIGNAL'), (company(id='co_old'), 'COMPANY_ID')]
        for c, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(b.BatchError, code):
                b.validate(packet(c))

    def test_rejects_shared_identifier_inside_the_packet(self):
        same = [{'scheme': 'EU_PIC', 'value': '1'}]
        with self.assertRaisesRegex(b.BatchError, 'IDENTIFIER_DUPLICATE'):
            b.validate(packet(company(1, identifiers=same), company(2, identifiers=same)))

    def encoded(self):
        raw = json.dumps(packet()).encode()
        return base64.b64encode(gzip.compress(raw)).decode(), hashlib.sha256(raw).hexdigest()

    def test_parts_are_joined_and_pinned(self):
        text, pin = self.encoded()
        env = {'EXP016_IMPORT_PACKET_1': text[:20], 'EXP016_IMPORT_PACKET_2': text[20:]}
        self.assertEqual(len(r.load_packet(env, pin)['companies']), 2)
        with self.assertRaisesRegex(b.BatchError, 'PACKET_PIN'):
            r.load_packet(env, 'c' * 64)
        with self.assertRaisesRegex(b.BatchError, 'PACKET_MISSING'):
            r.load_packet({}, pin)


class Boundary(unittest.TestCase):
    ENV = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'nab0k/bohun-the-field', 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
           'EXPECTED_SHA': 'a' * 40, 'GITHUB_SHA': 'a' * 40, 'GITHUB_WORKFLOW_SHA': 'a' * 40, 'IMPORT_MODE': 'write', 'IMPORT_CONFIRMATION': 'CANDIDATES_WRITE_1_BATCH'}

    def guard(self, production=False, **extra):
        with mock.patch('sys.platform', 'linux'):
            r.guard({**self.ENV, **extra}, production=production)

    def test_exact_context_passes(self):
        self.guard()
        self.guard(production=True, SUPABASE_DB_PASSWORD='x')

    def test_each_mode_needs_its_own_phrase(self):
        for mode, phrase in (('write', 'CANDIDATES_DRY_RUN'), ('rollback', 'CANDIDATES_WRITE_1_BATCH'), ('other', 'x')):
            with self.subTest(mode=mode), self.assertRaisesRegex(b.BatchError, 'CLOUD_MANUAL'):
                self.guard(IMPORT_MODE=mode, IMPORT_CONFIRMATION=phrase)

    def test_password_only_in_the_production_step(self):
        with self.assertRaisesRegex(b.BatchError, 'DATABASE_PASSWORD'):
            self.guard(SUPABASE_DB_PASSWORD='x')
        with self.assertRaisesRegex(b.BatchError, 'DATABASE_PASSWORD'):
            self.guard(production=True)

    def test_other_credentials_are_refused(self):
        with self.assertRaisesRegex(b.BatchError, 'OTHER_CREDENTIAL'):
            self.guard(NOTION_TOKEN='x')

    def test_refuses_outside_the_cloud(self):
        with mock.patch('sys.platform', 'darwin'), self.assertRaisesRegex(b.BatchError, 'CLOUD_MANUAL'):
            r.guard(self.ENV)

    def test_main_prints_only_a_bounded_code(self):
        with mock.patch.object(r, 'guard', side_effect=ValueError('private host and row')), mock.patch('builtins.print') as printed:
            self.assertEqual(r.main(['rehearse']), 1)
        printed.assert_called_once_with('IMPORT_FAILED:ValueError')

    def test_workflow_shape(self):
        text = (Path(__file__).parents[2] / '.github/workflows/exp016-candidate-import.yml').read_text()
        self.assertNotIn('schedule', text)
        self.assertEqual(text.count('secrets.SUPABASE_DB_PASSWORD'), 1)
        self.assertLess(text.index('run.py rehearse'), text.index('secrets.SUPABASE_DB_PASSWORD'))
        self.assertNotIn('upload-artifact', text)

    def test_batch_module_has_no_network_or_promotion(self):
        tree = ast.parse((Path(__file__).parent / 'batch.py').read_text())
        names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertEqual(names, {'hashlib', 'json'})
        text = (Path(__file__).parent / 'batch.py').read_text()
        self.assertNotIn("'card'", text.replace("c.get('status', 'candidate') == 'candidate'", ''))
        self.assertIsNone(re.search(r'UPDATE company\b', text))


if __name__ == '__main__':
    unittest.main()
