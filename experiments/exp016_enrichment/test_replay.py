"""Repeat, crash recovery, privacy and actual existing-module reuse tests."""
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest

from adapters import no_network
from baseline import Baseline
from contracts import digest
from evidence import observe, proposal
from report import comparison_html, report_data, write_report
from runner import execute, RESULTS, checked_output
from sources import project_manifest, receipt, reuse_module
from store import Ledger
from test_core import fixture


class Replay(unittest.TestCase):
    def setUp(self):
        RESULTS.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.temp = tempfile.TemporaryDirectory(prefix='tests-', dir=RESULTS)
        self.directory = Path(self.temp.name)
        self.data = fixture()

    def tearDown(self):
        self.temp.cleanup()

    def run_fixture(self, **kwargs):
        with no_network():
            return execute(self.data, self.directory, **kwargs)

    def test_repeat_produces_zero_extra_records_or_changes(self):
        first = self.run_fixture()
        second = self.run_fixture(mode='replay')
        self.assertEqual(first.after, second.after)
        self.assertEqual(first.observations, second.observations)
        self.assertEqual(first.changes, second.changes)
        self.assertEqual(second.counts['observed_this_invocation'], 0)
        self.assertEqual(second.counts['accepted_this_invocation'], 0)
        self.assertGreater(second.counts['duplicate_observations'], 0)

    def test_attribution_and_public_gates_preserved(self):
        result = self.run_fixture()
        alpha = result.after['alpha']
        self.assertFalse(alpha['public_release'])
        self.assertEqual(alpha['qa_status'], 'DRAFT')
        self.assertEqual(alpha['evidence']['products.product']['source_type'], 'company_self_claim')
        self.assertFalse(alpha['evidence']['products.product']['independent_confirmation'])
        self.assertEqual(result.after['beta']['qa_status'], 'HOLD')
        self.assertNotIn('technologies', result.after['beta']['fields'])

    def test_discovery_new_candidates_not_canonical_verified_companies(self):
        result = self.run_fixture()
        new = [r for key, r in result.after.items() if key.startswith('candidate-')]
        self.assertEqual(len(new), 2)
        self.assertTrue(all(r['qa_status'] == 'DRAFT' and not r['public_release'] for r in new))
        evidence = [e for r in new for e in r['evidence'].values()]
        self.assertEqual({e['modality'] for e in evidence}, {'planned', 'announced'})
        self.assertEqual(result.counts['real_companies_processed'], 0)

    def test_incomplete_union_keeps_discovery_unknown(self):
        self.data['baseline']['completeness']['aliases'] = False
        result = self.run_fixture()
        self.assertFalse(any(k.startswith('candidate-') for k in result.after))
        self.assertTrue(any(r.get('status') == 'UNKNOWN_NOVELTY' for r in result.holds))

    def test_failed_transaction_rolls_back_observation_and_profile(self):
        with self.assertRaisesRegex(RuntimeError, 'INJECTED'):
            self.run_fixture(fail_after_observation=True)
        ledger = Ledger(self.directory / 'ledger.sqlite3')
        try:
            self.assertEqual(ledger.all('observations'), [])
            self.assertEqual(ledger.all('changes'), [])
            self.assertEqual(ledger.get('profiles', 'alpha')['fields'], {})
        finally:
            ledger.close()
        recovered = self.run_fixture()
        self.assertGreater(recovered.counts['accepted_this_invocation'], 0)

    def test_partial_resume(self):
        partial = self.run_fixture(stop_after=1)
        self.assertEqual(partial.status, 'PARTIAL_CHECKPOINT')
        done = self.run_fixture(mode='replay')
        self.assertGreater(done.counts['observations_total'], partial.counts['observations_total'])
        self.assertEqual(done.counts['duplicate_observations'], 1)

    def test_input_mutation_rejected(self):
        self.run_fixture()
        self.data['sources'][0]['claims'][0]['value'] = 'new undocumented input'
        with self.assertRaisesRegex(ValueError, 'INPUT_CHANGED'):
            self.run_fixture(mode='replay')

    def test_rejected_source_transaction_is_not_reported_as_accepted(self):
        self.data['sources'] = self.data['sources'][:1]
        self.data['sources'][0]['claims'].append({'invalid': 'missing required keys'})
        result = self.run_fixture()
        self.assertEqual(result.status, 'PARTIAL_INPUT')
        self.assertEqual(result.counts['observed_this_invocation'], 0)
        self.assertEqual(result.counts['accepted_this_invocation'], 0)
        self.assertEqual(result.after['alpha']['fields'], {})

    def test_in_run_group_subsidiary_conflict_held(self):
        import copy
        candidate = copy.deepcopy(self.data['candidates'][3])
        candidate.update(candidate_id='same-id-wrong-group', entity_kind='group')
        self.data['candidates'].append(candidate)
        source = copy.deepcopy(self.data['sources'][4])
        source.update(source_id='wrong-group-source', candidate_id=candidate['candidate_id'])
        self.data['sources'].append(source)
        self.data['source_allowances'].append(source['source_id'])
        self.data['trusted_bindings'][source['source_id']] = candidate['identifiers']
        result = self.run_fixture()
        self.assertTrue(any('IN_RUN_GROUP_SUBSIDIARY_CONFLICT' in h.get('reason', '') for h in result.holds))
        self.assertEqual(result.counts['new_candidates_this_invocation'], 2)

    def test_expected_old_guard(self):
        baseline = Baseline(self.data['baseline'])
        ledger = Ledger(self.directory / 'guard.sqlite3')
        source = self.data['sources'][0]
        rec, _, _ = receipt(source, self.data['source_allowances'])
        obs = observe('alpha', source['claims'][0], source, rec)
        p = proposal(obs, baseline.row('alpha'), baseline.row('alpha'))
        try:
            ledger.seed(baseline, {'synthetic': True})
            with ledger.db:
                ledger.apply(p, obs)
            with self.assertRaisesRegex(ValueError, 'EXPECTED_OLD'):
                ledger.apply(p, obs)
        finally:
            ledger.close()

    def test_existing_manual_edit_not_overwritten(self):
        self.run_fixture(stop_after=1)
        ledger = Ledger(self.directory / 'ledger.sqlite3')
        try:
            alpha = ledger.get('profiles', 'alpha')
            alpha['fields'].setdefault('geography', {})['country'] = 'manual-country'
            with ledger.db:
                ledger.db.execute('UPDATE profiles SET body=? WHERE id=?', (json.dumps(alpha), 'alpha'))
        finally:
            ledger.close()
        result = self.run_fixture(mode='replay')
        self.assertEqual(result.after['alpha']['fields']['geography']['country'], 'manual-country')
        self.assertTrue(any('CONCURRENT_OR_MANUAL_EDIT' in c['reasons'] for c in result.changes))

    def test_pdf_is_not_parsed(self):
        self.data['sources'][0].update(format='pdf', raw='synthetic PDF not parsed')
        result = self.run_fixture()
        self.assertTrue(any('NOT_PARSED' in c['reasons'] for c in result.changes))

    def test_report_minimises_ua_geodata_and_is_inert(self):
        result = self.run_fixture()
        data = report_data(result)
        page = comparison_html(data)
        self.assertNotIn('SYNTHETIC_UA_CITY_PRIVATE', json.dumps(data))
        self.assertIn('default-src', page)
        self.assertNotIn('<script', page)
        bad = dict(data, counts={'text': '<img src=x onerror=alert(1)>'})
        self.assertNotIn('<img', comparison_html(bad))
        self.assertIn('&lt;img', comparison_html(bad))

    def test_private_files_and_no_report_overwrite(self):
        result = self.run_fixture()
        write_report(result, self.directory)
        for name in ('report.json', 'report.html', 'ledger.sqlite3'):
            self.assertEqual(stat.S_IMODE((self.directory / name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        with self.assertRaises(FileExistsError):
            write_report(result, self.directory)

    def test_output_escape_and_symlink_rejected(self):
        with self.assertRaisesRegex(ValueError, 'PRIVATE_RESULT'):
            checked_output(Path('/private/tmp/exp016-unapproved-result'))
        link = self.directory / 'linked'
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'SYMLINK'):
            checked_output(link)

    def test_complete_project_closure_guard_and_original_edf_taxonomy(self):
        manifest = project_manifest()
        self.assertIn('collectors/companies/model.py', manifest['files'])
        self.assertIn('collectors/intelligence_pipeline/contracts.py', manifest['files'])
        with no_network():
            edf = reuse_module('collectors.companies.sources.edf', manifest)
            record = edf.Grants.parse(self.data['edf_metadata'])
            self.assertEqual(record.source_key, 'SYN-PROJECT')
            self.assertEqual(record.payload['participants'][0]['pic'], '000000001')
            taxonomy = reuse_module('collectors.companies.taxonomy', manifest)
            self.assertIn(('capability', 'UNCR', 'CPV:35613000'), taxonomy.classify('CPV', '35613000'))
        broken = dict(manifest, files={})
        with self.assertRaisesRegex(ValueError, 'CLOSURE_CHANGED'):
            reuse_module('collectors.companies.sources.edf', broken)

    def test_original_http_adapter_injected_response_zero_socket(self):
        manifest = project_manifest()
        with no_network():
            mod = reuse_module('collectors.companies.sources.http', manifest)
            calls = []
            def opener(req, timeout):
                calls.append((req.full_url, timeout))
                return io.BytesIO(b'{"synthetic":true}')
            client = mod.JsonHttp(timeout=20, retries=0, opener=opener)
            self.assertEqual(client.get_json('https://synthetic.example/fixture'), {'synthetic': True})
            self.assertEqual(calls, [('https://synthetic.example/fixture', 20)])

    def test_native_not_run_is_partial_not_success(self):
        from adapters import native_status
        if any(r['status'] != 'READY_FOR_TEST' for r in native_status().values()):
            result = self.run_fixture(mode='native-fixture')
            self.assertEqual(result.status, 'PARTIAL_NATIVE')
            self.assertTrue(any(r['status'] == 'NATIVE_NOT_RUN' for r in result.native_calls.values()))
            self.assertEqual(result.model_transport_attempts, 0)


if __name__ == '__main__': unittest.main()
