"""A26 and original shared backend in the ordinary package; fake RPC only."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from collectors.companies.automation.event_triggers import core, Trigger, trigger_records
from collectors.companies.automation.profile_bridge import CardRead, SharedBackendLink, dispatch, prepare_batch
from collectors.companies.automation import checkpoint as codec, postgres as shared


NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def original_backend():
    return shared


class RpcDouble:
    """Deterministic RPC responses; not a PostgreSQL implementation."""
    def __init__(self, card):
        self.card, self.checkpoint = card, codec.encode_checkpoint(codec.empty_checkpoint())
        self.calls, self.batches, self.insert_counts = [], set(), []
        self.lose_ack = self.fail_lookup = self.conflict = False
        self.ownership_complete = True

    def __call__(self, action, request):
        self.calls.append(action)
        args = request['args']
        if action == 'read_current':
            data = {'revision': core.digest(self.checkpoint), 'policy_hash': core.Policy().fingerprint,
                    'company_ids': [self.card.company.company_id], 'cards': [{
                        'company': codec.encode(self.card.company), 'properties_json': self.card.properties_json,
                        'read_at': NOW.isoformat(), 'complete': True,
                        'manual_ownership_complete': self.ownership_complete}], 'checkpoint': self.checkpoint}
        elif action == 'commit_batch':
            if self.conflict:
                data = {'state': 'CONFLICT'}
            else:
                self.checkpoint = args['checkpoint']
                self.batches.add(args['batch_key'])
                self.insert_counts.append(len(args['jobs']))
                if self.lose_ack:
                    self.lose_ack = False
                    raise TimeoutError('simulated_lost_ack')
                data = {'state': 'APPLIED', 'batch_key': args['batch_key']}
        elif action == 'lookup_batch':
            if self.fail_lookup:
                raise TimeoutError('simulated_lookup_failure')
            data = ({'state': 'APPLIED', 'batch_key': args['batch_key']}
                    if args['batch_key'] in self.batches else None)
        else:
            raise AssertionError('unexpected RPC operation')
        return {'wire_version': codec.VERSION, 'status': 'OK', 'data': data}


class BackendCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.company = core.CompanySnapshot('co-compat', core.digest('base'))
        self.card = CardRead(self.company, core.json_text({'Country': 'DE'}), NOW, True)
        self.one = Trigger('co-compat', 'origin-1', core.digest('source-1'))
        self.two = Trigger('co-compat', 'origin-2', core.digest('source-2'),
                           ('PROGRAMMES', 'EVENTS', 'EVENTS'))

    def prepare(self, ts, cp=None):
        return prepare_batch(ts, (self.card,), cp or codec.empty_checkpoint(),
                             policy=core.Policy(), as_of=NOW)

    def checkpoint(self, prepared):
        return {k: prepared[k] for k in codec.CP_KEYS}

    def test_canonical_descriptors_deduplicate_and_reorder(self):
        a = trigger_records([self.two, self.one, self.two])
        b = trigger_records([self.one, self.two])
        self.assertEqual(a, b)
        self.assertEqual(len(a), 2)
        self.assertEqual(next(r for r in a if r['origin_id'] == 'origin-2')['affected_sections'],
                         ['EVENTS', 'PROGRAMMES'])

    def test_wire_descriptor_has_enough_data_to_recompute_original_key(self):
        p = self.prepare([self.one, self.two])
        rows = codec.decode(codec.encode(p['trigger_records']))
        for row in rows:
            rebuilt = Trigger(row['company_id'], row['origin_id'], row['content_hash'],
                              tuple(row['affected_sections']))
            self.assertEqual(rebuilt.key, row['key'])
            self.assertNotEqual(replace(rebuilt, origin_id='altered-origin').key, row['key'])

    def test_original_job_and_checkpoint_codec_roundtrip(self):
        p = self.prepare([self.one])
        cp = self.checkpoint(p)
        self.assertEqual(codec.decode_checkpoint(codec.encode_checkpoint(cp)), cp)
        jobs = codec.decode(codec.encode(p['jobs']))
        self.assertEqual(tuple(jobs), p['jobs'])
        self.assertTrue(all(type(j) is core.JobSpec for j in jobs))

    def test_repeat_after_original_checkpoint_roundtrip_has_zero_jobs(self):
        p = self.prepare([self.one, self.two])
        cp = codec.decode_checkpoint(codec.encode_checkpoint(self.checkpoint(p)))
        q = self.prepare([self.two, self.one], cp)
        self.assertEqual(q['jobs'], ())
        self.assertEqual(q['trigger_records'], p['trigger_records'])
        self.assertEqual(q['existing_keys'], p['existing_keys'])

    def test_planner_invalidation_does_not_rewrite_review_metadata(self):
        a = self.prepare([self.one])
        states = {key: core.SectionState('HAS_APPROVED_CLAIMS', NOW+timedelta(days=3), h)
                  for key, h in a['section_inputs'].items()}
        cp = {**self.checkpoint(a), 'existing_keys': frozenset(), 'section_states': states}
        p = self.prepare([self.one], cp)
        self.assertTrue(p['jobs'])
        self.assertEqual(p['section_states'], states)
        self.assertEqual(codec.decode_checkpoint(codec.encode_checkpoint(self.checkpoint(p)))
                         ['section_states'], states)

    def test_generator_input_keeps_trigger_descriptor(self):
        p = self.prepare(t for t in (self.one, self.two))
        self.assertEqual(len(p['trigger_records']), 2)
        self.assertEqual(p['checks'][0]['trigger_versions'], 2)

    def test_mixed_companies_survive_checkpoint_scope_roundtrip(self):
        other = replace(self.card, company=replace(self.company, company_id='co-other'))
        t = replace(self.one, company_id='co-other')
        p = prepare_batch((self.one, t), (self.card, other), codec.empty_checkpoint(),
                          policy=core.Policy(), as_of=NOW)
        cp = codec.decode_checkpoint(codec.encode_checkpoint(self.checkpoint(p)))
        codec.scope_checkpoint(cp, {'co-compat', 'co-other'})
        self.assertEqual({r['company_id'] for r in p['trigger_records']}, {'co-compat', 'co-other'})
        with self.assertRaises(core.ContractError):
            codec.scope_checkpoint(cp, {'co-compat'})

    def test_original_codec_rejects_corrupted_checkpoint(self):
        p = self.prepare([self.one])
        wire = codec.encode_checkpoint(self.checkpoint(p))
        wire['profile_hashes'].append(dict(wire['profile_hashes'][0]))
        with self.assertRaises(core.ContractError):
            codec.decode_checkpoint(wire)

    def linked(self, rpc):
        return SharedBackendLink(original_backend(), rpc, triggers=(self.one,),
                                 policy=core.Policy(), clock=lambda: NOW)

    def test_original_backend_dispatch_and_repeat(self):
        rpc = RpcDouble(self.card)
        backend = self.linked(rpc)
        first = dispatch((self.one,), backend, policy=core.Policy(), as_of=NOW)
        second = dispatch((self.one,), backend, policy=core.Policy(), as_of=NOW)
        self.assertEqual((first['status'], second['status']), ('APPLIED', 'APPLIED'))
        self.assertEqual(rpc.insert_counts, [10, 0])

    def test_live_clock_is_read_after_the_current_read(self):
        rpc = RpcDouble(self.card)
        calls = []
        def clock():
            calls.append(list(rpc.calls))
            return NOW
        result = dispatch((self.one,), self.linked(rpc), policy=core.Policy(), as_of=clock)
        self.assertEqual(result['status'], 'APPLIED')
        self.assertEqual(calls, [['read_current']])

    def test_original_backend_lost_ack_uses_lookup_not_second_commit(self):
        rpc = RpcDouble(self.card); rpc.lose_ack = True
        backend = self.linked(rpc)
        result = dispatch((self.one,), backend, policy=core.Policy(), as_of=NOW)
        self.assertEqual(result['status'], 'APPLIED')
        self.assertEqual(rpc.calls, ['read_current', 'commit_batch', 'lookup_batch'])
        self.assertTrue(core.is_hash(backend.last_batch))

    def test_unresolved_commit_stays_unknown_with_shared_receipt(self):
        rpc = RpcDouble(self.card); rpc.lose_ack = rpc.fail_lookup = True
        backend = self.linked(rpc)
        result = dispatch((self.one,), backend, policy=core.Policy(), as_of=NOW)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(rpc.calls.count('commit_batch'), 1)
        self.assertTrue(core.is_hash(backend.last_batch))

    def test_original_backend_requires_manual_ownership_read(self):
        rpc = RpcDouble(self.card); rpc.ownership_complete = False
        result = dispatch((self.one,), self.linked(rpc), policy=core.Policy(), as_of=NOW)
        self.assertEqual(result['status'], 'HOLD')
        self.assertEqual(rpc.calls, ['read_current'])

    def test_original_backend_version_conflict_does_not_retry(self):
        rpc = RpcDouble(self.card); rpc.conflict = True
        result = dispatch((self.one,), self.linked(rpc), policy=core.Policy(), as_of=NOW)
        self.assertEqual(result['reason'], 'CURRENT_VERSION_CHANGED')
        self.assertEqual(rpc.calls, ['read_current', 'commit_batch'])

    def test_trigger_descriptor_tamper_rejected_before_shared_commit(self):
        rpc = RpcDouble(self.card); backend = self.linked(rpc)
        revision, cards, checkpoint = backend.read_current(('co-compat',))
        p = prepare_batch((self.one,), cards, checkpoint, policy=core.Policy(), as_of=NOW)
        p['trigger_records'] = tuple({**r, 'origin_id': 'tampered'} for r in p['trigger_records'])
        with self.assertRaises(core.ContractError):
            backend.commit_batch(revision, p)
        self.assertEqual(rpc.calls, ['read_current'])


if __name__ == '__main__':
    unittest.main()
