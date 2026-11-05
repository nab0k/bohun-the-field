"""Offline only: canned pages and answers, stdlib. Real LangExtract runs in the cloud self-check."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from unittest import mock

from experiments.exp016_live_pilot import live_extract as lx, live_runner as r, live_seal as seal
from experiments.exp016_live_pilot.live_sources import Budget, LimitReached, fetch_source

NOW = '2026-11-05T02:00:00+00:00'
TEXT = 'Rotor GmbH makes radar sensors. Rotor GmbH plans a plant. Other Corp makes boats.'


def claim(quote, section='products', field='product', subject='Rotor GmbH', modality='asserted', text=TEXT):
    start = text.find(quote)
    return {'text': quote, 'start': start if start >= 0 else None, 'end': start + len(quote) if start >= 0 else None,
            'attributes': {'subject': subject, 'section': section, 'field': field, 'modality': modality}}


def case(kind='existing', qa=None, url='https://rotor.example.test/', source_type='company_self_claim', **kw):
    return {'id': 'co_rotor', 'name': 'Rotor GmbH', 'kind': kind, 'stratum': 'S', 'qa_status': qa, 'country': 'DE',
            'fields': {}, 'sources': [{'url': url, 'source_type': source_type}], **kw}


def page(text=TEXT):
    return {'status': 'CAPTURED', 'text': text, 'received_at': NOW, 'text_sha256': 'a' * 64, 'html_sha256': 'b' * 64}


def run_one(item, claims, fetched=None):
    return r.process({'cases': [item]}, fetch=lambda url: fetched or page(), extract=lambda t, c: (claims, None, False))


def only(result):
    return result['cases'][0]['proposals'][0]


class Admission(unittest.TestCase):
    def test_literal_claim_is_useful_but_always_held(self):
        p = only(run_one(case(), [claim('Rotor GmbH makes radar sensors')]))
        self.assertEqual(p['status'], 'HOLD')
        self.assertTrue(p['useful'])
        for reason in ('SEMANTIC_REVIEW_REQUIRED', 'IDENTITY_NOT_CONFIRMED', 'SELF_CLAIM'):
            self.assertIn(reason, p['reasons'])
        self.assertNotIn('WRONG_SUBJECT', p['reasons'])

    def test_quote_that_is_not_in_the_page_is_not_useful(self):
        bad = {**claim('Rotor GmbH makes radar sensors'), 'text': 'Rotor GmbH makes missiles'}
        p = only(run_one(case(), [bad]))
        self.assertIn('NON_LITERAL_SPAN', p['reasons'])
        self.assertFalse(p['useful'])

    def test_missing_span_is_not_useful(self):
        p = only(run_one(case(), [claim('Not in the text at all')]))
        self.assertFalse(p['useful'])

    def test_claim_about_another_company_is_not_useful(self):
        p = only(run_one(case(), [claim('Other Corp makes boats', subject='Other Corp')]))
        self.assertIn('SUBJECT_MISMATCH', p['reasons'])
        self.assertFalse(p['useful'])

    def test_field_outside_the_canvas_is_not_useful(self):
        p = only(run_one(case(), [claim('Rotor GmbH makes radar sensors', field='invented')]))
        self.assertIn('FIELD_NOT_IN_CANVAS', p['reasons'])
        self.assertFalse(p['useful'])

    def test_plan_stays_a_plan(self):
        p = only(run_one(case(), [claim('Rotor GmbH plans a plant', section='events', field='event', modality='planned')]))
        self.assertIn('PLANNED_NOT_FACT', p['reasons'])
        self.assertEqual(p['modality'], 'planned')

    def test_qa_hold_record_is_gated(self):
        p = only(run_one(case(qa='HOLD'), [claim('Rotor GmbH makes radar sensors')]))
        self.assertIn('RECORD_GATE', p['reasons'])

    def test_existing_value_is_not_overwritten(self):
        item = case(fields={'products': {'product': 'old'}})
        p = only(run_one(item, [claim('Rotor GmbH makes radar sensors')]))
        self.assertEqual(p['operation'], 'update')
        self.assertIn('NONEMPTY_REPLACEMENT', p['reasons'])

    def test_ukrainian_location_is_redacted(self):
        item = {**case(), 'country': 'UA'}
        p = only(run_one(item, [claim('Rotor GmbH makes radar sensors', section='geography', field='city')]))
        self.assertEqual(p['quote'], '[REDACTED_UA_LOCATION]')

    def test_financial_claim_needs_context(self):
        p = only(run_one(case(), [claim('Rotor GmbH makes radar sensors', section='financials', field='revenue')]))
        self.assertIn('FINANCIAL_CONTEXT_REQUIRED', p['reasons'])

    def test_source_field_does_not_count_as_useful(self):
        p = only(run_one(case(), [claim('Rotor GmbH makes radar sensors', section='sources', field='reference')]))
        self.assertFalse(p['useful'])


class Subject(unittest.TestCase):
    def test_legal_form_alone_is_no_match(self):
        self.assertFalse(r.subject_matches('Rotor GmbH', 'Other GmbH'))

    def test_brand_matches_legal_name(self):
        self.assertTrue(r.subject_matches('Rotor GmbH', 'Rotor'))

    def test_empty_subject_is_no_match(self):
        self.assertFalse(r.subject_matches('Rotor GmbH', None))


class Limits(unittest.TestCase):
    def test_post_limit(self):
        budget = Budget(sleep=lambda s: None, max_posts=1)
        budget.post()
        with self.assertRaisesRegex(LimitReached, 'MODEL_POST_LIMIT'):
            budget.post()

    def test_posts_keep_an_interval(self):
        waits, now = [], [100.0]
        budget = Budget(clock=lambda: now[0], sleep=waits.append)
        budget.post()
        now[0] += 5
        budget.post()
        self.assertEqual(waits, [10.0])

    def test_wall_limit(self):
        now = [0.0]
        budget = Budget(clock=lambda: now[0])
        now[0] = 7200.0
        with self.assertRaisesRegex(LimitReached, 'WALL_LIMIT'):
            budget.document()

    def test_page_failure_is_a_code_without_remote_text(self):
        def fail(url, policy, clock=None):
            raise ValueError('secret remote text')
        self.assertEqual(fetch_source('https://a.example.test/', Budget(), capture_fn=fail)['status'], 'WEB_REJECTED')

    def test_real_capture_refuses_other_url(self):
        from collectors.web_monitor.content import WebPolicy, capture
        policy = WebPolicy(allowed_urls=('https://a.example.test/',), rights_reference='x', primary=True)
        with self.assertRaisesRegex(ValueError, 'WEB_URL_NOT_ALLOWLISTED'):
            capture('https://b.example.test/', policy, fetch=lambda *a, **k: self.fail('network'))

    def test_one_redirect_stays_a_failure(self):
        from collectors.web_monitor.content import NoRedirect
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None, None, 301, '', {}, 'https://other.example.test/')


def http_error(code):
    return urllib.error.HTTPError('https://x', code, 'x', {}, None)


class Transport(unittest.TestCase):
    def answer(self, text='{"extractions": []}'):
        return {'candidates': [{'content': {'parts': [{'text': 'thinking', 'thought': True}, {'text': text}]}}],
                'usageMetadata': {'totalTokenCount': 7}}

    def test_key_goes_in_a_header_and_model_is_fixed(self):
        seen = {}
        def post(url, body, key):
            seen.update(url=url, key=key, body=body)
            return self.answer()
        t = lx.GeminiTransport('free-key', Budget(sleep=lambda s: None), post=post)
        self.assertEqual(t('p'), '{"extractions": []}')
        self.assertEqual(seen['key'], 'free-key')
        self.assertNotIn('free-key', seen['url'])
        self.assertIn('gemini-3.5-flash-lite', seen['url'])
        self.assertNotIn('tools', seen['body'])

    def test_missing_key_stops_before_a_request(self):
        with self.assertRaisesRegex(lx.ModelStopped, 'FREE_KEY_MISSING'):
            lx.GeminiTransport('', Budget())

    def test_paid_key_stops_the_run(self):
        with mock.patch.dict('os.environ', {'GEMINI_API_KEY': 'paid'}), self.assertRaisesRegex(lx.ModelStopped, 'PAID_KEY_PRESENT'):
            lx.free_key()

    def test_quota_refusal_stops_without_a_second_post(self):
        for code in (401, 403, 429):
            calls = []
            def post(url, body, key):
                calls.append(1)
                raise http_error(code)
            t = lx.GeminiTransport('k', Budget(sleep=lambda s: None), post=post)
            with self.assertRaises(lx.ModelStopped):
                t('p')
            self.assertEqual((t.stopped, len(calls)), ('MODEL_HTTP_' + str(code), 1))

    def test_server_error_is_one_failure_not_a_retry(self):
        calls = []
        def post(url, body, key):
            calls.append(1)
            raise http_error(503)
        t = lx.GeminiTransport('k', Budget(sleep=lambda s: None), post=post)
        with self.assertRaisesRegex(lx.ModelFailed, 'MODEL_HTTP_503'):
            t('p')
        self.assertEqual((t.stopped, len(calls)), (None, 1))

    def test_prompt_marks_page_as_untrusted(self):
        self.assertIn('untrusted', lx.prompt_for('Rotor'))


def fake_langextract(text, company, transport):
    answer = json.loads(transport('page:' + text))
    return [claim(q, text=text) for q in answer['quotes']]


class Pipeline(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(lx, 'run_langextract', fake_langextract)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.pages = []

    def capture(self, url, policy, clock=None):
        self.pages.append(url)
        return {'url': url, 'received_at': NOW, 'text': TEXT, 'text_sha256': 'a' * 64, 'html_sha256': 'b' * 64}

    def post(self, url, body, key):
        return {'candidates': [{'content': {'parts': [{'text': json.dumps({'quotes': ['Rotor GmbH makes radar sensors']})}]}}]}

    def packet(self, n=2):
        return {'cases': [{**case(url=f'https://r{i}.example.test/'), 'id': f'co_{i}'} for i in range(n)]}

    def budget(self, **kw):
        return Budget(sleep=lambda s: None, **kw)

    def test_full_pass_counts_and_replay_changes_nothing(self):
        budget = self.budget()
        out = r.run_pilot(self.packet(), key='k', capture_fn=self.capture, run=self.post, budget=budget)
        self.assertEqual(out['counts']['existing_with_useful_proposal'], 2)
        self.assertEqual(out['counts']['accepted'], 0)
        self.assertEqual((budget.documents, budget.posts), (2, 2))
        self.assertEqual(out['replay'], {'new_observations': 0, 'new_changes': 0, 'same_keys': True,
                                         'network_documents': 0, 'network_posts': 0})
        self.assertEqual(len(self.pages), 2)

    def test_quota_stop_leaves_later_sources_not_run(self):
        def post(url, body, key):
            raise http_error(429)
        out = r.run_pilot(self.packet(3), key='k', capture_fn=self.capture, run=post, budget=self.budget())
        self.assertEqual(out['counts']['stopped'], 'MODEL_HTTP_429')
        self.assertEqual(out['counts']['model_posts'], 1)
        self.assertEqual(len(self.pages), 1)
        self.assertEqual(out['counts']['source_status'].get('NOT_RUN_MODEL_HTTP_429'), 2)

    def test_post_limit_stops_the_run(self):
        out = r.run_pilot(self.packet(3), key='k', capture_fn=self.capture, run=self.post, budget=self.budget(max_posts=2))
        self.assertEqual(out['counts']['stopped'], 'MODEL_POST_LIMIT')
        self.assertEqual(out['counts']['model_posts'], 2)

    def test_failed_page_does_not_call_the_model(self):
        def fail(url, policy, clock=None):
            raise ValueError('WEB_ROBOTS_DENIED')
        out = r.run_pilot(self.packet(1), key='k', capture_fn=fail, run=self.post, budget=self.budget())
        self.assertEqual(out['counts']['source_status'], {'WEB_ROBOTS_DENIED': 1})
        self.assertEqual(out['counts']['model_posts'], 0)

    def test_bad_model_answer_is_recorded_and_the_run_continues(self):
        answers = iter([{'candidates': [{'content': {'parts': [{'text': 'not json'}]}}]}])
        def post(url, body, key):
            return next(answers, None) or self.post(url, body, key)
        out = r.run_pilot(self.packet(2), key='k', capture_fn=self.capture, run=post, budget=self.budget())
        self.assertEqual(out['counts']['model_status'], {'EXTRACT_PARSE_FAILED': 1, 'OK': 1})
        self.assertFalse(out['counts']['stopped'])

    def test_long_page_is_cut_before_the_model(self):
        long_text = TEXT + ' x' * 9000
        seen = []
        def capture(url, policy, clock=None):
            return {'url': url, 'received_at': NOW, 'text': long_text}
        def post(url, body, key):
            seen.append(body['contents'][0]['parts'][0]['text'])
            return self.post(url, body, key)
        out = r.run_pilot(self.packet(1), key='k', capture_fn=capture, run=post, budget=self.budget())
        self.assertLessEqual(len(seen[0]), lx.MAX_TEXT_CHARS + len('page:'))
        self.assertTrue(out['cases'][0]['sources'][0]['truncated'])

    def test_controls_and_candidates_are_counted_apart(self):
        packet = {'cases': [{**case(kind='control'), 'id': 'c'}, {**case(kind='new'), 'id': 'n'}]}
        out = r.run_pilot(packet, key='k', capture_fn=self.capture, run=self.post, budget=self.budget())
        counts = out['counts']
        self.assertEqual((counts['existing_with_useful_proposal'], counts['controls_with_useful_proposal'],
                          counts['new_with_useful_proposal']), (0, 1, 1))

    def test_report_escapes_page_text(self):
        hostile = '<script>alert(1)</script> Rotor GmbH makes radar sensors'
        result = run_one(case(), [claim('<script>alert(1)</script>', text=hostile)], fetched=page(hostile))
        result['counts'] = {}
        self.assertNotIn('<script>', r.report_html(result))


class Boundary(unittest.TestCase):
    ENV = {'GITHUB_ACTIONS': 'true', 'RUNNER_OS': 'Linux', 'GITHUB_REPOSITORY': 'nab0k/bohun-the-field',
           'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
           'PILOT_CONFIRMATION': r.CONFIRMATION, 'EXPECTED_SHA': 'a' * 40, 'GITHUB_SHA': 'a' * 40,
           'GITHUB_WORKFLOW_SHA': 'a' * 40}

    def guard(self, **extra):
        with mock.patch('sys.platform', 'linux'):
            r.guard({**self.ENV, **extra})

    def test_exact_cloud_context_passes(self):
        self.guard()

    def test_refuses_outside_the_cloud(self):
        with mock.patch('sys.platform', 'darwin'), self.assertRaisesRegex(RuntimeError, 'CLOUD_MANUAL'):
            r.guard(self.ENV)

    def test_refuses_other_sha_and_missing_confirmation(self):
        for extra in ({'GITHUB_SHA': 'b' * 40}, {'PILOT_CONFIRMATION': 'yes'}):
            with self.assertRaisesRegex(RuntimeError, 'CLOUD_MANUAL'):
                self.guard(**extra)

    def test_refuses_working_credentials(self):
        for name in ('SUPABASE_DB_PASSWORD', 'NOTION_TOKEN', 'GEMINI_API_KEY', 'GROQ_API_KEY'):
            with self.assertRaisesRegex(RuntimeError, 'WORKING_CREDENTIAL'):
                self.guard(**{name: 'x'})

    def packet(self, value=None):
        raw = json.dumps(value or {'format': 'exp016.live_pilot.v1', 'cases': []}).encode()
        return base64.b64encode(gzip.compress(raw)).decode(), hashlib.sha256(raw).hexdigest()

    def test_packet_pin(self):
        encoded, pin = self.packet()
        self.assertEqual(r.load_packet(encoded, pin)['cases'], [])
        with self.assertRaisesRegex(RuntimeError, 'PACKET_PIN'):
            r.load_packet(encoded, 'c' * 64)
        with self.assertRaisesRegex(RuntimeError, 'PACKET_MISSING'):
            r.load_packet('', pin)

    def test_main_prints_only_a_bounded_code(self):
        with mock.patch.object(r, 'selfcheck', side_effect=ValueError('private https://x/ text')), \
                mock.patch('builtins.print') as printed:
            self.assertEqual(r.main(['selfcheck']), 1)
        printed.assert_called_once_with('PILOT_FAILED:ValueError')

    def test_workflow_gives_secrets_only_to_the_pilot_step(self):
        text = (Path(__file__).parents[2] / '.github/workflows/exp016-live-pilot.yml').read_text()
        self.assertEqual(sorted(re.findall(r'secrets\.(\w+)', text)), ['EXP016_PILOT_PACKET_B64', 'GEMINI_FREE_API_KEY'])
        self.assertLess(text.index('selfcheck'), text.index('secrets.'))
        self.assertNotIn('schedule', text)
        self.assertRegex(text, r'upload-artifact@[0-9a-f]{40}')
        self.assertIn('result.cms', text)

    def test_no_writer_or_second_provider_in_the_pilot(self):
        import ast
        allowed = {'argparse', 'base64', 'collections', 'datetime', 'gzip', 'html', 'json', 'os', 'pathlib', 're', 'sys',
                   'time', 'hashlib', 'shutil', 'subprocess', 'urllib', 'contracts', 'evidence', 'experiments', 'collectors',
                   'langextract'}
        urls = set()
        for name in ('live_runner.py', 'live_sources.py', 'live_extract.py', 'live_seal.py'):
            tree = ast.parse((Path(__file__).parent / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertLessEqual({a.name.split('.')[0] for a in node.names}, allowed, name)
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    self.assertIn(node.module.split('.')[0], allowed, name)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    urls.update(re.findall(r'https://[^/\s\'"]+', node.value))
        self.assertLessEqual({u for u in urls if not u.endswith('.example.test')}, {'https://generativelanguage.googleapis.com'})


@unittest.skipUnless(shutil.which('openssl'), 'openssl required')
class Seal(unittest.TestCase):
    def test_roundtrip_and_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            key, cert = Path(tmp) / 'k.pem', Path(tmp) / 'c.pem'
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-subj', '/CN=TEST',
                            '-keyout', str(key), '-out', str(cert)], check=True, capture_output=True)
            key.chmod(0o600)
            pin = seal.sha(cert.read_bytes())
            cipher = seal.seal(b'private report', cert, pin)
            self.assertNotIn(b'private report', cipher)
            self.assertEqual(seal.unseal(cipher, key, cert), b'private report')
            with self.assertRaisesRegex(RuntimeError, 'CERTIFICATE_PIN'):
                seal.seal(b'x', cert, 'c' * 64)
            with self.assertRaisesRegex(RuntimeError, 'CERTIFICATE_PIN'):
                seal.seal(b'x', key, seal.sha(key.read_bytes()))


if __name__ == '__main__':
    unittest.main()
