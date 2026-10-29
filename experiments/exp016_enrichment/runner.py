"""Synthetic-only CLI; no live/deploy modes, database or source credentials."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time

from adapters import (NativeUnavailable, ftm_fixture, langextract_fixture, native_status,
                      no_network, nomenklatura_fixture, trafilatura_fixture)
from baseline import Baseline
from contracts import Budget, LimitReached, RunManifest, RunResult, digest
from evidence import observe, proposal, validate
from identity import resolve
from planner import queues, tasks
from report import private_write, write_report
from sources import project_manifest, receipt
from store import Ledger

HERE = Path(__file__).resolve().parent
RESULTS = HERE / 'results' / 'cloud-native'


def checked_output(directory):
    directory = Path(directory)
    if not directory.resolve().is_relative_to(RESULTS.resolve()) or directory == RESULTS:
        raise ValueError('PRIVATE_RESULT_SUBDIRECTORY_REQUIRED')
    if any(p.is_symlink() for p in (directory, *directory.parents)):
        raise ValueError('OUTPUT_SYMLINK')
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    return directory


def source_hashes():
    import hashlib
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(HERE.iterdir()) if p.is_file() and p.suffix in {'.py', '.json', '.md'}}


def native_smoke(data, project):
    results = {}
    calls = {'followthemoney': ftm_fixture, 'nomenklatura': nomenklatura_fixture,
             'trafilatura': lambda: trafilatura_fixture(data['native_html'], project),
             'langextract': lambda: langextract_fixture(data['native_text'], data['native_outputs'])}
    with no_network():
        for name, call in calls.items():
            try:
                results[name] = {'status': 'PASS', 'result': call()}
            except NativeUnavailable as exc:
                results[name] = {'status': 'NATIVE_NOT_RUN', 'reason': str(exc)}
            except Exception as exc:
                results[name] = {'status': 'FAILED', 'reason': type(exc).__name__ + ':' + str(exc)}
    return results


def execute(data, directory, mode='offline', *, stop_after=None, fail_after_observation=False):
    started = time.monotonic()
    directory = checked_output(directory)
    if mode not in {'offline', 'replay', 'native-fixture'} or data.get('synthetic') is not True:
        raise ValueError('SYNTHETIC_STAGE_ONLY')
    baseline = Baseline(data['baseline'])
    store = Ledger(directory / 'ledger.sqlite3')
    try:
        old_manifest = store.get('metadata', 'manifest')
        manifest = asdict(RunManifest(
            run_id=data['run_id'], code_hashes=source_hashes(),
            dependency_hash=digest(json.loads((HERE / 'dependencies.json').read_text())),
            baseline_hash=baseline.hash, baseline_checked_at=baseline.checked_at,
            completeness=baseline.completeness, fixed_existing=tuple(data['fixed_existing']),
            source_allowances=tuple(data['source_allowances']),
            deadline=(old_manifest['deadline'] if old_manifest else
                      (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()),
            mode=mode))
        # Canonical JSON shape; tuple/list representation must not change on restart.
        manifest = json.loads(json.dumps(manifest))
        if old_manifest and old_manifest['mode'] in {'offline', 'replay'} and mode in {'offline', 'replay'}:
            manifest['mode'] = old_manifest['mode']
        store.seed(baseline, manifest)
        old_input = store.get('metadata', 'input_hash')
        if old_input and old_input != digest(data):
            raise ValueError('INPUT_CHANGED_DURING_RUN')
        with store.db:
            store.put('metadata', 'input_hash', digest(data))
        budget = Budget(manifest['deadline'], store.get('metadata', 'budget'))
        existing, discovery = queues(baseline, data['fixed_existing'], data['candidates'], data['source_allowances'])
        with store.db:
            for task in existing + discovery:
                store.put('jobs', digest(asdict(task)), asdict(task))
        counts = {'observed_this_invocation': 0, 'accepted_this_invocation': 0,
                  'held_this_invocation': 0, 'duplicate_observations': 0,
                  'processed_sources_this_invocation': 0, 'new_candidates_this_invocation': 0}
        holds = []
        status = 'SUCCEEDED_SYNTHETIC'
        for source in data['sources']:
            try:
                rec, raw_size, text_size = receipt(source, data['source_allowances'])
                budget.admit_document(rec.key, raw_size, text_size, source['format'])
                if source['candidate_id'] in baseline.rows and source['candidate_id'] not in data['fixed_existing']:
                    raise ValueError('OUTSIDE_FIXED_COHORT')
                candidate = next(c for c in data['candidates'] if c['candidate_id'] == source['candidate_id'])
                binding = resolve(candidate, baseline)
                if binding['status'] == 'NEW_CANDIDATE':
                    subject = 'candidate-' + digest(candidate['identifiers'])[:20]
                    new_row = {'id': subject, 'identifiers': candidate['identifiers'],
                               'entity_kind': candidate['entity_kind'], 'qa_status': 'DRAFT',
                               'public_release': False, 'excluded': False, 'fields': {}}
                    queue = candidate.get('queue')
                    if queue not in manifest['quotas']:
                        raise ValueError('DISCOVERY_QUEUE_REQUIRED')
                elif binding['status'] == 'BOUND':
                    subject = binding['subject']
                    if subject not in data['fixed_existing']:
                        raise ValueError('OUTSIDE_FIXED_COHORT')
                    new_row = baseline.row(subject)
                else:
                    holds.append({'source_id': source['source_id'], **binding})
                    with store.db:
                        store.finish_jobs(source['candidate_id'], 'identity', 'registration_id', binding['status'])
                    continue
                pending_counts = {'observed_this_invocation': 0, 'accepted_this_invocation': 0,
                                  'held_this_invocation': 0, 'duplicate_observations': 0,
                                  'new_candidates_this_invocation': 0}
                with store.db:
                    existing_copy = store.get('profiles', subject)
                    if existing_copy and existing_copy.get('entity_kind') != candidate.get('entity_kind'):
                        raise ValueError('IN_RUN_GROUP_SUBSIDIARY_CONFLICT')
                    if binding['status'] == 'NEW_CANDIDATE' and not store.get('profiles', subject):
                        used = store.get('metadata', 'discovery_counts') or {'news': 0, 'grants_tenders': 0}
                        if used[queue] >= manifest['quotas'][queue]:
                            raise LimitReached('DISCOVERY_QUOTA')
                        used[queue] += 1
                        store.metadata('discovery_counts', used)
                        store.put('profiles', subject, new_row)
                        pending_counts['new_candidates_this_invocation'] += 1
                        for task in tasks(new_row, data['source_allowances'], queue='discovery'):
                            store.put('jobs', digest(asdict(task)), asdict(task))
                    store.finish_jobs(source['candidate_id'], 'identity', 'registration_id', binding['status'])
                    store.put('receipts', rec.key, asdict(rec))
                    for claim in source['claims']:
                        observation = observe(subject, claim, source, rec)
                        if not store.put('observations', observation.key, asdict(observation)):
                            pending_counts['duplicate_observations'] += 1
                            continue
                        if fail_after_observation:
                            raise RuntimeError('INJECTED_TRANSACTION_FAILURE')
                        current = store.get('profiles', subject)
                        reasons = validate(observation, source, rec, current, data['trusted_bindings'])
                        change = proposal(observation, new_row, current, reasons)
                        store.put('changes', change.key, asdict(change))
                        applied = store.apply(change, observation)
                        store.finish_jobs(subject, observation.section, observation.field, change.status)
                        pending_counts['observed_this_invocation'] += 1
                        pending_counts['accepted_this_invocation'] += int(applied)
                        pending_counts['held_this_invocation'] += int(change.status == 'HOLD')
                    store.metadata('budget', budget.snapshot())
                for key, value in pending_counts.items():
                    counts[key] += value
                counts['processed_sources_this_invocation'] += 1
                if stop_after and counts['processed_sources_this_invocation'] >= stop_after:
                    status = 'PARTIAL_CHECKPOINT'
                    break
            except LimitReached as exc:
                holds.append({'source_id': source['source_id'], 'reason': str(exc)})
                status = 'PARTIAL_LIMIT'
                break
            except (ValueError, KeyError, StopIteration) as exc:
                holds.append({'source_id': source.get('source_id'), 'reason': type(exc).__name__ + ':' + str(exc)})
                status = 'PARTIAL_INPUT'
        with store.db:
            store.metadata('budget', budget.snapshot())
        after = {r['id']: r for r in store.all('profiles')}
        observations, changes = store.all('observations'), store.all('changes')
        unknowns = [asdict(t) for row in after.values() for t in tasks(row, data['source_allowances'])
                    if t.reason == 'missing']
        counts.update({'observations_total': len(observations), 'changes_total': len(changes),
                       'useful_gain_total': sum(c['status'] == 'ACCEPTED_PRIVATE' and
                                                c['section'] != 'sources' for c in changes),
                       'held_total': sum(c['status'] == 'HOLD' for c in changes),
                       'unknown_fields_total': len(unknowns), 'synthetic_existing_count': len(baseline.rows),
                       'real_companies_processed': 0})
        native = native_smoke(data, project_manifest()) if mode == 'native-fixture' else {}
        limitations = ['Synthetic fixture replay only; not live30+10 or accuracy evaluation.',
                       'Unstructured text requires semantic review; no generic semantic classifier validated.',
                       'No production/Notion/site writer. This is a Linux synthetic-only native smoke.']
        if native and not all(r['status'] == 'PASS' for r in native.values()):
            status = 'PARTIAL_NATIVE'
        return RunResult(data['run_id'], mode, status, True, baseline.rows, after, observations,
                         changes, unknowns, holds, counts, baseline.completeness,
                         time.monotonic() - started, native_calls=native, limitations=limitations)
    finally:
        store.close()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('offline', 'replay', 'native-fixture', 'verify-cloud'))
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.mode == 'verify-cloud':
        cloud_verify(args.output)
        return
    data = json.loads((HERE / 'fixtures.json').read_text())
    directory = checked_output(args.output)
    with no_network():
        result = execute(data, directory, args.mode)
    # Preserve reports from earlier invocations rather than overwrite them.
    report_dir = directory / ('report-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f'))
    report_dir.mkdir(mode=0o700)
    write_report(result, report_dir)
    print(json.dumps({'status': result.status, 'counts': result.counts, 'report': str(report_dir)}, ensure_ascii=False))


def cloud_verify(output):
    """Future Linux-only execution: native skips and all evidence mismatches fail the job."""
    # Deliberate lazy import: the build helper is not executed/imported by Mac core tests.
    from cloud_environment import context
    context()
    import unittest
    output = checked_output(output)
    if any(output.iterdir()):
        raise ValueError('NEW_CLOUD_PROOF_DIRECTORY_REQUIRED')
    data = json.loads((HERE / 'fixtures.json').read_text())
    counters = {}
    with no_network():
        for label, modules, minimum in (('core_replay_tests', ('test_core', 'test_replay'), 55),
                                        ('native_tests', ('test_native',), 4)):
            suite = unittest.defaultTestLoader.loadTestsFromNames(modules)
            with (output / (label + '.log')).open('x') as log:
                result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
            counters[label] = {'run': result.testsRun, 'passed': result.testsRun - len(result.errors)
                               - len(result.failures) - len(result.skipped), 'skipped': len(result.skipped),
                               'errors': len(result.errors), 'failures': len(result.failures)}
            if (not result.wasSuccessful() or result.skipped or result.testsRun < minimum
                    or label == 'native_tests' and result.testsRun != 4):
                private_write(output / 'proof.json', json.dumps({'status': 'FAILED', **counters}, indent=2))
                raise ValueError('STRICT_TEST_GATE:' + label)
        first = execute(data, output / 'repeat', 'offline')
        second = execute(data, output / 'repeat', 'replay')
        same = (first.after == second.after and first.observations == second.observations
                and first.changes == second.changes and second.counts['observed_this_invocation'] == 0
                and second.counts['accepted_this_invocation'] == 0)
        native = execute(data, output / 'native', 'native-fixture')
        all_native = len(native.native_calls) == 4 and all(r['status'] == 'PASS' for r in native.native_calls.values())
        if not same or not all_native or native.status != 'SUCCEEDED_SYNTHETIC':
            private_write(output / 'proof.json', json.dumps({'status': 'FAILED', **counters,
                          'repeat_equal': same, 'native_smoke': native.native_calls}, indent=2))
            raise ValueError('NATIVE_OR_REPEAT_PROOF_REQUIRED')
        write_report(first, output / 'repeat')
        write_report(native, output / 'native')
        proof = {'status': 'PASS', **counters, 'repeat_equal': True,
                 'repeat_new_observations': 0, 'repeat_new_accepted': 0,
                 'native_smoke': native.native_calls, 'native_status': native_status(),
                 'code_hashes': source_hashes(), 'project_sources': project_manifest(),
                 'synthetic': True, 'real_companies': 0, 'model_transport_attempts': 0,
                 'business_http_attempts': 0, 'paid_model_cost': 0,
                 'time_seconds': first.actual_seconds + second.actual_seconds + native.actual_seconds,
                 'semantic_accuracy_on_real_companies': 'NOT_RUN'}
        import hashlib
        proof['workflow_sha256'] = hashlib.sha256((HERE.parents[1] /
            '.github/workflows/exp016-enrichment-native.yml').read_bytes()).hexdigest()
        private_write(output / 'proof.json', json.dumps(proof, ensure_ascii=False, indent=2))
    print(json.dumps({'status': 'PASS', 'native_tests': counters['native_tests'], 'repeat_equal': True}))


if __name__ == '__main__':
    main()
