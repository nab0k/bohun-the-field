"""Offline: canned pages, redirects and answers. Real pages and the model run only in the cloud."""
import base64
import gzip
import hashlib
import json
from pathlib import Path
import re
import unittest
from unittest import mock
import urllib.error

from experiments.exp016_edf_cards import cards as cd, fetch as fh, run as r
from experiments.exp016_live_pilot import live_extract as lx
from experiments.exp016_live_pilot.live_sources import Budget

TEXT = 'Arbit develops data diodes for classified networks. We serve defence and intelligence customers since 2006.'
COMPANY = {'id': 'co_c208_1', 'name': 'Arbit ApS', 'country': 'DK', 'website': 'https://arbit.test/',
           'projects': [{'acronym': 'SESIOP', 'call': 'EDF-2022-RA', 'signed': '2023-12-13', 'role': 'participant', 'eur': '1', 'sme': False}]}


def item(text, quote):
    return {'text': text, 'quote': quote}


GOOD = {'summary': [item('Makes data diodes', 'Arbit develops data diodes for classified networks')],
        'products': [item('Data diode', 'data diodes for classified networks')], 'markets': [item('Defence', 'We serve defence and intelligence customers')],
        'claims': [item('Since 2006', 'intelligence customers since 2006')], 'class': 'defence', 'class_quote': 'We serve defence and intelligence customers'}


class Check(unittest.TestCase):
    def test_quoted_items_are_kept(self):
        kept, dropped = cd.check(GOOD, TEXT)
        self.assertEqual((len(kept['summary']), dropped, kept['class']), (1, 0, 'defence'))

    def test_invented_quote_is_dropped(self):
        data = {**GOOD, 'summary': [item('Makes missiles', 'Arbit makes missiles for the army')]}
        kept, dropped = cd.check(data, TEXT)
        self.assertEqual((kept['summary'], dropped), ([], 1))

    def test_whitespace_and_case_do_not_matter(self):
        self.assertTrue(cd.found('ARBIT  develops\ndata diodes', TEXT))

    def test_tiny_quote_is_not_evidence(self):
        self.assertFalse(cd.found('Arbit', TEXT))

    def test_defence_class_needs_its_own_quote(self):
        kept, _ = cd.check({**GOOD, 'class_quote': 'invented defence words here'}, TEXT)
        self.assertEqual(kept['class'], 'unclear')

    def test_unknown_class_becomes_unclear(self):
        kept, _ = cd.check({**GOOD, 'class': 'weapons'}, TEXT)
        self.assertEqual(kept['class'], 'unclear')

    def test_lists_are_capped(self):
        many = {**GOOD, 'claims': [item('x', 'Arbit develops data diodes')] * 20}
        kept, dropped = cd.check(many, TEXT)
        self.assertEqual(len(kept['claims']), cd.MAX_ITEMS['claims'])

    def test_prompt_marks_page_untrusted_and_is_cut(self):
        p = cd.prompt_for('Arbit', 'x' * 20000)
        self.assertIn('untrusted', p)
        self.assertLess(len(p), cd.MAX_TEXT_CHARS + 3000)

    def test_verdicts(self):
        cap = {'status': 'CAPTURED', 'url': 'https://arbit.test/'}
        self.assertEqual(cd.build(COMPANY, cap, cd.check(GOOD, TEXT)[0])['verdict'], 'likely_fit')
        self.assertEqual(cd.build(COMPANY, {'status': 'WEB_FETCH_FAILED_NO_RETRY'})['verdict'], 'no_description')
        med = cd.check({**GOOD, 'class': 'medical_cbrn_lab'}, TEXT)[0]
        self.assertEqual(cd.build(COMPANY, cap, med)['verdict'], 'likely_off_profile')
        self.assertEqual(cd.build(COMPANY, cap, {**med, 'class': 'unclear'})['verdict'], 'edf_only')


class Redirects(unittest.TestCase):
    def probe(self, table):
        return lambda url: table.get(url)

    def test_no_redirect(self):
        self.assertEqual(fh.resolve('http://arbit.test', probe=self.probe({})), ('https://arbit.test/', False))

    def test_www_redirect_is_followed_once(self):
        table = {'https://arbit.test/': 'https://www.arbit.test/en/'}
        self.assertEqual(fh.resolve('https://arbit.test/', probe=self.probe(table)), ('https://www.arbit.test/en/', True))

    def test_other_site_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'OTHER_SITE'):
            fh.resolve('https://arbit.test/', probe=self.probe({'https://arbit.test/': 'https://parked.example/'}))

    def test_chain_is_refused(self):
        table = {'https://arbit.test/': 'https://www.arbit.test/', 'https://www.arbit.test/': 'https://arbit.test/x'}
        with self.assertRaisesRegex(ValueError, 'CHAIN'):
            fh.resolve('https://arbit.test/', probe=self.probe(table))

    def test_probe_reads_location_without_following(self):
        class Opener:
            def open(self, req, timeout):
                raise urllib.error.HTTPError(req.full_url, 301, 'moved', {'Location': '/en/'}, None)
        self.assertEqual(fh.redirect_target('https://arbit.test/', opener=Opener(), check_host=lambda u: None), 'https://arbit.test/en/')

    def test_fetch_home_reports_a_refusal_as_a_code(self):
        page = fh.fetch_home('https://arbit.test/', Budget(), probe=self.probe({'https://arbit.test/': 'https://other.test/'}))
        self.assertEqual(page['status'], 'WEB_REDIRECT_OTHER_SITE')


def capture(url, policy, clock=None):
    return {'url': url, 'received_at': '2026-11-05T00:00:00+00:00', 'text': TEXT, 'text_sha256': 'a' * 64, 'html_sha256': 'b' * 64}


class Run(unittest.TestCase):
    def packet(self, n=2):
        return {'cases': [], 'companies': [{**COMPANY, 'id': f'co_{i}', 'website': f'https://s{i}.test/'} for i in range(n)]}

    def fetch(self, budget):
        return lambda url: fh.fetch_home(url, budget, probe=lambda u: None, capture_fn=capture)

    def transport(self, answers, budget):
        t = lx.GeminiTransport('k', budget)
        it = iter(answers)
        def post(url, body, key):
            a = next(it)
            if isinstance(a, Exception):
                raise a
            return {'candidates': [{'content': {'parts': [{'text': a}]}}]}
        t.post = post
        return t

    def test_full_pass(self):
        b = Budget(sleep=lambda s: None)
        cards, stopped = r.run(self.packet(), transport=self.transport([json.dumps(GOOD)] * 2, b), budget=b, fetch=self.fetch(b))
        totals = r.counts(cards, b, stopped)
        self.assertEqual((totals['with_summary'], totals['verdict'], b.posts), (2, {'likely_fit': 2}, 2))

    def test_quota_stops_and_marks_the_rest(self):
        b = Budget(sleep=lambda s: None)
        err = urllib.error.HTTPError('x', 429, 'x', {}, None)
        cards, stopped = r.run(self.packet(3), transport=self.transport([err], b), budget=b, fetch=self.fetch(b))
        self.assertEqual((stopped, b.posts, cards[2]['site_status']), ('MODEL_HTTP_429', 1, 'NOT_RUN_MODEL_HTTP_429'))

    def test_bad_answer_is_one_failure(self):
        b = Budget(sleep=lambda s: None)
        cards, stopped = r.run(self.packet(2), transport=self.transport(['not json', json.dumps(GOOD)], b), budget=b, fetch=self.fetch(b))
        self.assertEqual(([c['model_status'] for c in cards], stopped), (['ANSWER_INVALID', 'OK'], None))

    def test_document_limit_stops_cleanly(self):
        b = Budget(sleep=lambda s: None, max_documents=1)
        cards, stopped = r.run(self.packet(3), transport=self.transport([json.dumps(GOOD)] * 3, b), budget=b, fetch=self.fetch(b))
        self.assertEqual((stopped, cards[1]['site_status']), ('DOCUMENT_LIMIT', 'NOT_RUN_DOCUMENT_LIMIT'))

    def test_report_escapes_page_text(self):
        hostile = {**GOOD, 'summary': [item('<script>x</script>', 'Arbit develops data diodes')]}
        card = cd.build(COMPANY, {'status': 'CAPTURED'}, cd.check(hostile, TEXT)[0])
        self.assertNotIn('<script>', r.report_html([card], {}))


class Boundary(unittest.TestCase):
    ENV = {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': 'nab0k/bohun-the-field', 'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_REF': 'refs/heads/main',
           'CARDS_CONFIRMATION': r.CONFIRMATION, 'EXPECTED_SHA': 'a' * 40, 'GITHUB_SHA': 'a' * 40, 'GITHUB_WORKFLOW_SHA': 'a' * 40}

    def guard(self, **extra):
        with mock.patch('sys.platform', 'linux'):
            r.guard({**self.ENV, **extra})

    def test_exact_context(self):
        self.guard()

    def test_refusals(self):
        for extra, code in (({'GITHUB_SHA': 'b' * 40}, 'CLOUD'), ({'CARDS_CONFIRMATION': 'yes'}, 'CLOUD'),
                            ({'SUPABASE_DB_PASSWORD': 'x'}, 'WORKING'), ({'GEMINI_API_KEY': 'x'}, 'WORKING')):
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, code):
                self.guard(**extra)

    def test_packet_parts_and_pin(self):
        raw = json.dumps({'format': 'exp016.edf_cards.v1', 'companies': []}).encode()
        text = base64.b64encode(gzip.compress(raw)).decode()
        env = {'EXP016_CARDS_PACKET_1': text[:9], 'EXP016_CARDS_PACKET_2': text[9:]}
        self.assertEqual(r.load_packet(env, hashlib.sha256(raw).hexdigest())['companies'], [])
        with self.assertRaisesRegex(RuntimeError, 'PACKET_PIN'):
            r.load_packet(env, 'c' * 64)

    def test_main_prints_a_bounded_code(self):
        with mock.patch.object(r, 'guard', side_effect=ValueError('private page text')), mock.patch('builtins.print') as printed:
            self.assertEqual(r.main(), 1)
        printed.assert_called_once_with('CARDS_FAILED:ValueError')

    def test_workflow_shape(self):
        text = (Path(__file__).parents[2] / '.github/workflows/exp016-edf-cards.yml').read_text()
        self.assertNotIn('schedule', text)
        self.assertNotIn('SUPABASE', text)
        self.assertLess(text.index('verify'), text.index('secrets.'))
        self.assertRegex(text, r'upload-artifact@[0-9a-f]{40}')


if __name__ == '__main__':
    unittest.main()
