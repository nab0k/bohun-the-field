"""Adversarial contract tests. These do not measure real-world extraction accuracy."""
import copy
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import unittest

from adapters import no_network, native_status
from baseline import Baseline, identifier_key, read_saved
from contracts import Budget, LimitReached, RunManifest, digest
from evidence import observe, proposal, validate
from identity import resolve
from planner import queues, tasks
from sources import json_field, receipt, saved_ner_candidates

HERE = Path(__file__).resolve().parent


def fixture():
    return json.loads((HERE / 'fixtures.json').read_text())


class Core(unittest.TestCase):
    def setUp(self):
        self.data = fixture()
        self.baseline = Baseline(self.data['baseline'])
        self.source = self.data['sources'][0]
        self.rec, _, _ = receipt(self.source, self.data['source_allowances'])
        self.obs = observe('alpha', self.source['claims'][0], self.source, self.rec)

    def reasons(self, obs=None, source=None):
        return validate(obs or self.obs, source or self.source, self.rec,
                        self.baseline.row('alpha'), self.data['trusted_bindings'])

    def test_self_claim_accepted_only_as_private_attributed_value(self):
        p = proposal(self.obs, self.baseline.row('alpha'), self.baseline.row('alpha'), self.reasons())
        self.assertEqual(p.status, 'ACCEPTED_PRIVATE')
        self.assertEqual(self.obs.source_type, 'company_self_claim')

    def test_name_country_hostname_never_bind_identity(self):
        c = {'name': 'Synthetic alpha', 'country': 'DE', 'hostname': 'alpha.example', 'identifiers': []}
        self.assertEqual(resolve(c, self.baseline)['status'], 'SUGGESTION')

    def test_identifier_scheme_jurisdiction(self):
        for key in ('scheme', 'jurisdiction', 'value'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                identifier_key({k: 'x' for k in ('scheme', 'jurisdiction', 'value') if k != key})

    def test_wrong_scheme_not_exact_match(self):
        c = copy.deepcopy(self.data['candidates'][0])
        c['identifiers'][0]['scheme'] = 'another-register'
        self.assertEqual(resolve(c, self.baseline)['status'], 'NEW_CANDIDATE')

    def test_conflicting_stable_ids_hold(self):
        c = copy.deepcopy(self.data['candidates'][0])
        c['identifiers'].append(dict(c['identifiers'][0], value='CONFLICT'))
        self.assertEqual(resolve(c, self.baseline)['reason'], 'IDENTIFIER_CONFLICT')

    def test_group_subsidiary_hold(self):
        c = dict(self.data['candidates'][0], entity_kind='group')
        self.assertEqual(resolve(c, self.baseline)['reason'], 'GROUP_SUBSIDIARY')

    def test_ambiguous_ids_hold(self):
        data = self.baseline.as_dict()
        data['companies'].append(dict(data['companies'][0], id='duplicate-legal-id'))
        self.assertEqual(resolve(self.data['candidates'][0], Baseline(data))['reason'], 'AMBIGUOUS_STRONG_ID')

    def test_incomplete_union_cannot_assert_novelty(self):
        for key in ('registry', 'notion', 'aliases', 'identifiers'):
            data = self.baseline.as_dict()
            data['completeness'][key] = False
            self.assertEqual(resolve(self.data['candidates'][3], Baseline(data))['status'], 'UNKNOWN_NOVELTY')

    def test_baseline_is_immutable(self):
        row = self.baseline.row('alpha')
        row['fields']['products'] = {'product': 'changed'}
        self.assertEqual(self.baseline.row('alpha')['fields'], {})

    def test_baseline_hash_guard(self):
        with self.assertRaisesRegex(ValueError, 'BASELINE_HASH'):
            read_saved(HERE / 'fixtures.json', 'wronghash')

    def test_individual_freshness_dates_required(self):
        data = self.baseline.as_dict()
        data['checked_at']['aliases'] = '2026-10-29'
        with self.assertRaisesRegex(ValueError, 'TIMEZONE'):
            Baseline(data)

    def test_ten_sections_and_unknown_tasks(self):
        self.assertEqual(len({t.section for t in tasks(self.baseline.row('alpha'), ())}), 10)

    def test_fixed_cohort_and_two_queues(self):
        old, new = queues(self.baseline, self.data['fixed_existing'], self.data['candidates'], ())
        self.assertTrue(old and new)
        self.assertEqual({t.queue for t in old}, {'existing'})
        self.assertEqual({t.queue for t in new}, {'discovery'})
        with self.assertRaisesRegex(ValueError, 'FIXED_COHORT'):
            queues(self.baseline, ['absent'], [], ())

    def test_source_allowance_and_rights(self):
        with self.assertRaisesRegex(ValueError, 'SOURCE_NOT_ALLOWED'):
            receipt(self.source, ())
        with self.assertRaisesRegex(ValueError, 'RIGHTS'):
            receipt(dict(self.source, rights_reference=''), self.data['source_allowances'])

    def test_source_url_and_live_input_blocked(self):
        for change in ({'url': 'http://alpha.example/'}, {'url': 'https://token:secret@alpha.example/'}, {'synthetic': False}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                receipt(dict(self.source, **change), self.data['source_allowances'])

    def test_received_timestamp_is_not_new_evidence(self):
        r, _, _ = receipt(dict(self.source, received_at='2026-10-29T11:00:00+00:00'), self.data['source_allowances'])
        self.assertEqual(r.key, self.rec.key)

    def test_source_change_is_new_receipt(self):
        s = copy.deepcopy(self.source)
        s['raw']['fields']['products']['product'] += ' changed'
        r, _, _ = receipt(s, self.data['source_allowances'])
        self.assertNotEqual(r.key, self.rec.key)

    def test_explicit_jsonpath(self):
        self.assertEqual(json_field({'a': {'b': 'yes'}}, '$.a.b'), 'yes')
        for path in ('a.b', '$.*', '$.a[0]', '$.missing'):
            with self.assertRaises(ValueError):
                json_field({'a': 1}, path)

    def test_wrong_source_value_held(self):
        self.assertIn('VALUE_NOT_AT_PATH', self.reasons(replace(self.obs, value='invented')))

    def test_wrong_subject_held(self):
        self.assertIn('WRONG_SUBJECT', self.reasons(replace(self.obs, subject_identifiers=({'scheme': 'x', 'jurisdiction': 'DE', 'value': 'x'},))))

    def test_source_type_cannot_be_promoted(self):
        self.assertIn('SOURCE_TYPE_MISMATCH', self.reasons(replace(self.obs, source_type='registry_record')))

    def test_inference_held(self):
        self.assertIn('INFERENCE_IS_NOT_FACT', self.reasons(replace(self.obs, source_type='inference_candidate')))

    def test_unknown_issuer_held(self):
        self.assertIn('ISSUER_NOT_BOUND', validate(self.obs, self.source, self.rec, self.baseline.row('alpha'), {}))

    def test_hypothetical_span_is_not_semantic_proof(self):
        source = dict(self.source, format='text', text='Synthetic Alpha does not make missiles.')
        obs = replace(self.obs, locator={'start': 0, 'end': len(source['text']), 'quote': source['text']}, modality='negative')
        self.assertIn('SEMANTIC_REVIEW_REQUIRED', self.reasons(obs, source))
        self.assertNotIn('NON_LITERAL_SPAN', self.reasons(obs, source))

    def test_hallucinated_or_fuzzy_span_held(self):
        source = dict(self.source, format='text', text='Original exact text')
        obs = replace(self.obs, locator={'start': 0, 'end': 19, 'quote': 'original exact text'})
        self.assertIn('NON_LITERAL_SPAN', self.reasons(obs, source))

    def test_financial_context_required(self):
        obs = replace(self.obs, section='financials', field='revenue', value=123)
        self.assertIn('FINANCIAL_CONTEXT_REQUIRED', self.reasons(obs))
        self.assertNotIn('FINANCIAL_CONTEXT_REQUIRED', self.reasons(replace(obs, currency='EUR', units='million', period='FY2025')))

    def test_grant_not_delivery_contract(self):
        obs = replace(self.obs, section='programmes_contracts', field='contract', source_type='official_programme_record')
        self.assertIn('PROGRAMME_NOT_DELIVERY_CONTRACT', self.reasons(obs))

    def test_modality_cannot_be_promoted_without_source_field(self):
        self.assertIn('MODALITY_NOT_BOUND_TO_SOURCE', self.reasons(replace(self.obs, modality='completed')))

    def test_accounting_parent_not_ubo(self):
        obs = replace(self.obs, section='relationships', field='ubo')
        self.assertIn('RELATION_TYPE_UNSUPPORTED', self.reasons(obs))

    def test_classification_needs_taxonomy_not_size(self):
        self.assertIn('TAXONOMY_REVIEW_REQUIRED', self.reasons(replace(self.obs, section='classification', field='category', value='PRIME')))

    def test_null_no_clear(self):
        row = self.baseline.row('alpha')
        row['fields'] = {'products': {'product': 'existing value'}}
        p = proposal(replace(self.obs, value=None), row, row)
        self.assertEqual((p.operation, p.status), ('clear', 'HOLD'))
        empty = self.baseline.row('alpha')
        self.assertEqual(proposal(replace(self.obs, value=None), empty, empty).operation, 'unchanged')

    def test_nonempty_replacement_and_manual_edit_hold(self):
        old = self.baseline.row('alpha')
        current = copy.deepcopy(old)
        current['fields'] = {'products': {'product': 'manual value'}}
        p = proposal(self.obs, old, current)
        self.assertEqual(p.status, 'HOLD')
        self.assertIn('CONCURRENT_OR_MANUAL_EDIT', p.reasons)
        self.assertIn('NONEMPTY_REPLACEMENT', p.reasons)

    def test_qa_excluded_and_field_gates(self):
        for extra in ({'qa_status':'HOLD'}, {'qa_status':'REJECT'}, {'excluded':True}, {'blocked_fields':['products.product']}):
            current = dict(self.baseline.row('alpha'), **extra)
            self.assertEqual(proposal(self.obs, current, current).status, 'HOLD')

    def test_budget_caps_and_persisted_counters(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        budget = Budget(future)
        for i in range(200): budget.admit_document(str(i), 1, 1, 'json')
        replay = Budget(future, budget.snapshot())
        replay.admit_document('0', 1, 1, 'json')
        with self.assertRaisesRegex(LimitReached, 'DOCUMENT_LIMIT'):
            replay.admit_document('200', 1, 1, 'json')
        for kind in ('model', 'http'):
            with self.assertRaises(LimitReached): replay.live_attempt(kind)

    def test_document_size_run_bytes_and_wall_caps(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        budget = Budget(future)
        for raw, text, kind in ((2*1024**2+1, 0, 'html'), (5*1024**2+1, 0, 'json'), (1, 128*1024+1, 'json')):
            with self.assertRaisesRegex(LimitReached, 'DOCUMENT_BYTES'):
                budget.admit_document('big', raw, text, kind)
        budget.state['bytes'] = 128*1024**2
        with self.assertRaisesRegex(LimitReached, 'RUN_BYTES'):
            budget.admit_document('overflow', 1, 0, 'json')
        budget.state['seconds'] = 7200
        with self.assertRaisesRegex(LimitReached, 'WALL_LIMIT'):
            budget.check()

    def test_deadline_stop(self):
        with self.assertRaisesRegex(LimitReached, 'DEADLINE'):
            Budget('2000-01-01T00:00:00+00:00').check()

    def test_network_block_and_no_model_recomputation(self):
        with no_network():
            with self.assertRaisesRegex(RuntimeError, 'NETWORK_FORBIDDEN'):
                socket.create_connection(('example.com', 443))
            with self.assertRaises(RuntimeError): socket.socket()
        self.assertEqual(saved_ner_candidates([{'name':'Alpha'}])[0]['identity_status'], 'SUGGESTION')


import os
import sys
import tempfile
from unittest.mock import patch


@unittest.skipUnless(sys.platform == 'linux' and os.environ.get('GITHUB_ACTIONS') == 'true',
                     'CLOUD_HELPER_NOT_EXECUTED_ON_MAC')
class CloudContracts(unittest.TestCase):
    """Future cloud-only adversarial helper tests, without build/download/install."""
    def test_rejects_unapproved_runtime_before_subprocess(self):
        from cloud_environment import context
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'false'}), patch('subprocess.run') as command:
            with self.assertRaisesRegex(ValueError, 'NO_MAC'):
                context()
            command.assert_not_called()

    def test_rejects_unknown_index_or_insecure_package_url(self):
        from cloud_environment import public_url
        for url in ('http://pypi.org/simple/a', 'https://pypi.org.attacker.example/a',
                    'https://user:secret@pypi.org/a', 'https://pypi.org:444/a', 'file:///tmp/pkg.whl'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                public_url(url)

    def test_incomplete_dependency_graph_not_lockable(self):
        from cloud_environment import validated_lock
        with self.assertRaisesRegex(ValueError, 'DEPENDENCY_COUNT'):
            validated_lock({'install': []}, Path('/no-such-wheel'))

    def test_lock_requires_own_pyicu_and_exact_four_pins(self):
        from cloud_environment import PINS, validated_lock, sha
        from runner import RESULTS
        RESULTS.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(prefix='cloud-lock-test-', dir=RESULTS) as temp:
            wheel = Path(temp) / 'pyicu-2.17-cp313-cp313-linux_x86_64.whl'
            wheel.write_bytes(b'synthetic wheel bytes, not installed')
            report = {'install': [{'metadata': {'name': name, 'version': ver},
                'download_info': {'url': wheel.as_uri() if name == 'pyicu' else
                    f'https://files.pythonhosted.org/packages/{name}-{ver}-py3-none-any.whl',
                    'archive_info': {'hashes': {'sha256': sha(wheel.read_bytes())}}}}
                for name, ver in PINS.items()]}
            self.assertIn('pyicu==2.17 --hash=sha256:', validated_lock(report, wheel))
            wrong = copy.deepcopy(report)
            wrong['install'][0]['metadata']['version'] = '999'
            with self.assertRaisesRegex(ValueError, 'DIRECT_PIN'):
                validated_lock(wrong, wheel)
            wrong = copy.deepcopy(report)
            wrong['install'][-1]['download_info']['url'] = 'https://files.pythonhosted.org/packages/pyicu-2.17.whl'
            with self.assertRaisesRegex(ValueError, 'BUILT_PYICU'):
                validated_lock(wrong, wheel)

    def test_source_build_and_bad_hash_cannot_enter_lock(self):
        from cloud_environment import PINS, validated_lock
        report = {'install': [{'metadata': {'name': n, 'version': v}, 'download_info': {
            'url': f'https://files.pythonhosted.org/{n}-{v}.tar.gz',
            'archive_info': {'hashes': {'sha256': '0' * 64}}}} for n, v in PINS.items()]}
        with self.assertRaisesRegex(ValueError, 'WHEEL_WITH_HASH'):
            validated_lock(report, Path('/not-read'))


if __name__ == '__main__': unittest.main()
