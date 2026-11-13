"""Offline: the same SQL statements on SQLite. Real PostgreSQL runs in the workflow rehearsal."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import unittest

from collectors.companies.store import SqliteStore
from experiments.exp016_descriptions_import import batch as b, run as r

AT = '2026-11-13T12:00:00+00:00'
PROFILE_DDL = """
CREATE TABLE profile_source(id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT NOT NULL REFERENCES company(id),
  source_ref TEXT NOT NULL, title TEXT NOT NULL, source_url TEXT NOT NULL CHECK(source_url LIKE 'https://%'),
  source_kind TEXT NOT NULL, as_of TEXT NOT NULL, original_bytes_archived INTEGER NOT NULL DEFAULT 0,
  snapshot_sha256 TEXT, captured_at TEXT, created_at TEXT, UNIQUE(company_id, source_ref, source_url), UNIQUE(company_id, id));
CREATE TABLE profile_claim(id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT NOT NULL REFERENCES company(id),
  claim_ref TEXT NOT NULL, field_path TEXT NOT NULL, claim_value TEXT NOT NULL,
  assessment TEXT NOT NULL CHECK(assessment IN ('registry_supported','cross_source','company_claim','inferred_needs_review')),
  review_note TEXT NOT NULL DEFAULT '', content_sha256 TEXT NOT NULL, qa_status TEXT NOT NULL DEFAULT 'HOLD',
  public_release INTEGER NOT NULL DEFAULT 0 CHECK(public_release = 0), created_at TEXT,
  UNIQUE(company_id, claim_ref, content_sha256), UNIQUE(company_id, id));
CREATE TABLE profile_claim_source(company_id TEXT NOT NULL, claim_id INTEGER NOT NULL, source_id INTEGER NOT NULL,
  PRIMARY KEY(claim_id, source_id), FOREIGN KEY(company_id, claim_id) REFERENCES profile_claim(company_id, id),
  FOREIGN KEY(company_id, source_id) REFERENCES profile_source(company_id, id));
"""


class Conn:
    def __init__(self):
        self.db = SqliteStore(':memory:').db
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript(PROFILE_DDL)

    def execute(self, sql, params=()):
        return self.db.execute(sql.replace('%s', '?').replace('::jsonb', ''), params)

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def add_company(self, cid, status='candidate'):
        self.db.execute('INSERT INTO company(id,name,name_norm,status,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                        (cid, cid, cid, status, AT, AT))


def claim(cid, n=1, value=None):
    value = value or f'Makes rotor parts {n}'
    return {'claim_ref': f'c209_summary_{n}', 'field_path': b.FIELD_PATH, 'value': value, 'assessment': 'company_claim',
            'quote': f'We make rotor parts {n}.', 'content_sha256': b.digest([cid, b.FIELD_PATH, value])}


def company(n=1, claims=2, **kw):
    cid = f'co_c208_{n:04}'
    c = {'company_id': cid, 'source': {'source_ref': b.SOURCE_REF, 'url': f'https://rotor{n}.test/', 'source_kind': 'company_website',
                                       'as_of': '2026-11-12', 'title': 'Company home page'},
         'claims': [claim(cid, i) for i in range(1, claims + 1)]}
    c.update(kw)
    return c


def packet(*companies):
    return {'format': 'exp016.c2_10.descriptions.v1', 'run_id': b.RUN_ID, 'source_sha256': '0' * 64,
            'companies': list(companies or (company(1), company(2, claims=1)))}


class Batch(unittest.TestCase):
    def setUp(self):
        self.c = Conn()
        for n in (1, 2):
            self.c.add_company(f'co_c208_{n:04}')

    def test_apply_counts_and_verify(self):
        p = packet()
        before = b.precheck(self.c, p)
        report = b.apply(self.c, p, AT)
        self.assertEqual(report, {'sources': 2, 'claims': 3, 'links': 3})
        b.verify(before, b.counts(self.c), report, p)

    def test_claims_are_hold_and_not_public_with_quote_in_note(self):
        b.apply(self.c, packet(), AT)
        rows = self.c.execute('SELECT qa_status, public_release, claim_value, review_note FROM profile_claim').fetchall()
        self.assertEqual({(x[0], x[1]) for x in rows}, {('HOLD', 0)})
        self.assertEqual(json.loads(rows[0][2]), 'Makes rotor parts 1')
        self.assertEqual(json.loads(rows[0][3])['quote'], 'We make rotor parts 1.')
        self.assertEqual(json.loads(rows[0][3])['run_id'], b.RUN_ID)

    def test_company_rows_are_not_touched(self):
        before = self.c.execute('SELECT * FROM company ORDER BY id').fetchall()
        b.apply(self.c, packet(), AT)
        self.assertEqual(self.c.execute('SELECT * FROM company ORDER BY id').fetchall(), before)

    def test_missing_company_stops_before_writing(self):
        with self.assertRaisesRegex(b.BatchError, 'TARGET_COMPANY_MISSING_OR_NOT_CANDIDATE'):
            b.precheck(self.c, packet(company(1), company(3)))
        self.assertEqual(b.counts(self.c)['profile_claim'], 0)

    def test_card_company_is_refused(self):
        self.c.add_company('co_c208_0005', status='card')
        with self.assertRaisesRegex(b.BatchError, 'TARGET_COMPANY_MISSING_OR_NOT_CANDIDATE'):
            b.precheck(self.c, packet(company(5)))

    def test_repeat_is_refused(self):
        b.apply(self.c, packet(), AT)
        with self.assertRaisesRegex(b.BatchError, 'BATCH_ALREADY_PRESENT'):
            b.precheck(self.c, packet())

    def test_undo_keeps_the_neighbours(self):
        cid = 'co_c208_0001'
        src = self.c.execute("INSERT INTO profile_source(company_id,source_ref,title,source_url,source_kind,as_of) "
                             "VALUES(?,'s_other','O','https://o.test/','company_website','2026-10-01') RETURNING id", (cid,)).fetchone()[0]
        cl = self.c.execute("INSERT INTO profile_claim(company_id,claim_ref,field_path,claim_value,assessment,content_sha256) "
                            "VALUES(?,'c13','/summary/primary_activity','\"x\"','company_claim',?) RETURNING id", (cid, '1' * 64)).fetchone()[0]
        self.c.execute('INSERT INTO profile_claim_source(company_id,claim_id,source_id) VALUES(?,?,?)', (cid, cl, src))
        base = b.counts(self.c)
        b.apply(self.c, packet(), AT)
        self.assertEqual(b.undo(self.c, packet()), {'removed_sources': 2, 'removed_claims': 3})
        self.assertEqual(b.counts(self.c), base)

    def test_undo_aborts_when_another_claim_uses_a_batch_source(self):
        b.apply(self.c, packet(), AT)
        cid = 'co_c208_0001'
        src = self.c.execute('SELECT id FROM profile_source WHERE source_ref=?', (b.SOURCE_REF,)).fetchone()[0]
        cl = self.c.execute("INSERT INTO profile_claim(company_id,claim_ref,field_path,claim_value,assessment,content_sha256) "
                            "VALUES(?,'c99','/summary/primary_activity','\"y\"','company_claim',?) RETURNING id", (cid, '2' * 64)).fetchone()[0]
        self.c.execute('INSERT INTO profile_claim_source(company_id,claim_id,source_id) VALUES(?,?,?)', (cid, cl, src))
        with self.assertRaises(Exception):
            b.undo(self.c, packet())

    def test_verify_catches_a_short_write(self):
        p = packet()
        before = b.precheck(self.c, p)
        report = b.apply(self.c, packet(company(1)), AT)
        with self.assertRaisesRegex(b.BatchError, 'PACKET_NOT_FULLY_WRITTEN'):
            b.verify(before, b.counts(self.c), report, p)


class Validate(unittest.TestCase):
    def bad(self, code, c):
        with self.assertRaisesRegex(b.BatchError, code):
            b.validate(packet(c))

    def test_wrong_format_or_run(self):
        with self.assertRaisesRegex(b.BatchError, 'PACKET_FORMAT'):
            b.validate(dict(packet(), run_id='1'))

    def test_foreign_company_id(self):
        self.bad('COMPANY_ID', dict(company(1), company_id='co_old_1'))

    def test_duplicate_company(self):
        with self.assertRaisesRegex(b.BatchError, 'COMPANY_ID'):
            b.validate(packet(company(1), company(1)))

    def test_http_or_spaced_url(self):
        self.bad('SOURCE_URL', company(1, source=dict(company(1)['source'], url='http://x.test/')))
        self.bad('SOURCE_URL', company(1, source=dict(company(1)['source'], url='https://x .test/')))

    def test_other_source_ref(self):
        self.bad('SOURCE_KIND', company(1, source=dict(company(1)['source'], source_ref='s_home')))

    def test_too_many_claims(self):
        self.bad('CLAIM_COUNT', company(1, claims=4))

    def test_other_field_or_assessment(self):
        cl = dict(claim('co_c208_0001'), field_path='/products/-')
        self.bad('CLAIM_KIND', company(1, claims=0) | {'claims': [cl]})
        cl = dict(claim('co_c208_0001'), assessment='cross_source')
        self.bad('CLAIM_KIND', company(1, claims=0) | {'claims': [cl]})

    def test_changed_value_breaks_the_hash(self):
        cl = dict(claim('co_c208_0001'), value='Something else')
        self.bad('CLAIM_HASH', company(1, claims=0) | {'claims': [cl]})

    def test_overlong_value(self):
        self.bad('CLAIM_VALUE', company(1, claims=0) | {'claims': [claim('co_c208_0001', value='x' * 401)]})


class Runner(unittest.TestCase):
    ENV = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': r.REPOSITORY, 'GITHUB_EVENT_NAME': 'workflow_dispatch',
           'GITHUB_REF': 'refs/heads/main', 'EXPECTED_SHA': 'a' * 40, 'GITHUB_SHA': 'a' * 40, 'GITHUB_WORKFLOW_SHA': 'a' * 40,
           'IMPORT_MODE': 'dry_run', 'IMPORT_CONFIRMATION': 'DESCRIPTIONS_DRY_RUN'}

    def guard(self, env, production=False):
        with unittest.mock.patch.object(r.sys, 'platform', 'linux'):
            r.guard(env, production=production)

    def test_guard_accepts_the_exact_manual_run(self):
        self.guard(self.ENV)
        self.guard(dict(self.ENV, SUPABASE_DB_PASSWORD='x'), production=True)

    def test_guard_refuses_wrong_phrase_sha_or_extra_keys(self):
        for env in (dict(self.ENV, IMPORT_CONFIRMATION='CANDIDATES_DRY_RUN'), dict(self.ENV, GITHUB_SHA='b' * 40),
                    dict(self.ENV, GEMINI_FREE_API_KEY='x'), dict(self.ENV, SUPABASE_DB_PASSWORD='x')):
            with self.assertRaises(b.BatchError):
                self.guard(env)
        with self.assertRaisesRegex(b.BatchError, 'DATABASE_PASSWORD_ONLY'):
            self.guard(self.ENV, production=True)

    def test_packet_pin_and_parts(self):
        raw = json.dumps(packet()).encode()
        enc = base64.b64encode(gzip.compress(raw)).decode()
        env = {r.PACKET_PREFIX + '1': enc[:50], r.PACKET_PREFIX + '2': enc[50:]}
        self.assertEqual(len(r.load_packet(env, hashlib.sha256(raw).hexdigest())['companies']), 2)
        with self.assertRaisesRegex(b.BatchError, 'PACKET_PIN'):
            r.load_packet(env, '0' * 64)
        with self.assertRaisesRegex(b.BatchError, 'PACKET_MISSING'):
            r.load_packet({}, '0' * 64)

    def test_plan_matches_the_counts(self):
        plan = r.load_plan()
        self.assertEqual((plan['batch'], plan['companies'], plan['claims']), (b.BATCH, 333, 870))

    def test_workflow_names_this_runner_and_its_secrets_only(self):
        wf = (r.ROOT / '.github/workflows/exp016-descriptions-import.yml').read_text()
        self.assertIn('experiments/exp016_descriptions_import/run.py production', wf)
        self.assertNotIn('EXP016_IMPORT_PACKET', wf)
        self.assertEqual(wf.count('secrets.SUPABASE_DB_PASSWORD'), 1)


import unittest.mock  # noqa: E402

if __name__ == '__main__':
    unittest.main()
