"""Fake queue interleavings exercise the port contract, not PostgreSQL concurrency."""
from dataclasses import replace
from datetime import timedelta
import sys
import unittest

from collectors.companies.automation.contracts import (CompanyView, ContractError, FieldValue,
                                                      JobSpec, Lease, Policy, digest)
from collectors.companies.automation.changes import check_preconditions
from collectors.companies.automation.worker import (
    CommitReceipt, ExternalOutcomeUnknown, LeaseLost, PreparedWork, QuotaUnavailable,
    SourceUnavailable, UnknownCommit, WorkContext, check_lease, run_one)
from companies_automation.test_changes import CID, NOW, POLICY, website_claim


class FakeQueue:
    """Test-only transaction boundary and process-independent state in one object."""
    def __init__(self, job=None, view=None):
        self.job = job or JobSpec("ENRICH_SECTION", POLICY.fingerprint, POLICY.version, digest("input"), CID, "IDENTITY")
        self.view = view or CompanyView(CID, country="DE")
        self.state = self.job.initial_state
        self.token = None
        self.attempt = 0
        self.expires = None
        self.prepared = None
        self.claims = {}
        self.receipts = {}
        self.commit_calls = 0
        self.defer_calls = []
        self.drop_commit_response = False
        self.hide_receipt = False
        self.abort_before_commit = False

    def claim(self, policy_hash, now, lease_seconds):
        if self.job.policy_hash != policy_hash:
            return None
        if self.state != "READY" and not (self.state == "LEASED" and self.expires <= now):
            return None
        self.attempt += 1
        self.token = f"synthetic-lease-{self.attempt}"
        self.expires = now+timedelta(seconds=lease_seconds)
        self.state = "LEASED"
        return Lease(self.job, self.token, self.expires, self.attempt)

    def context(self, lease):
        return WorkContext(self.view, self.job.input_hash)

    def fence(self, lease, now):
        check_lease(lease, token=self.token, now=now, policy=POLICY)
        if self.state != "LEASED":
            raise LeaseLost()

    def prepared_result(self, lease):
        return self.prepared

    def save_prepared(self, lease, changes, now):
        self.fence(lease, now)
        check_preconditions(changes, self.view, POLICY)
        if self.prepared and self.prepared.fingerprint != changes.fingerprint:
            raise ContractError("PREPARED_RESULT_IMMUTABLE")
        self.prepared = changes

    def commit_result(self, lease, changes, now):
        self.commit_calls += 1
        key = (lease.job.job_key, changes.fingerprint)
        if key in self.receipts:
            return self.receipts[key]
        self.fence(lease, now)
        check_preconditions(changes, self.view, POLICY)
        if self.abort_before_commit:
            self.abort_before_commit = False
            raise UnknownCommit()
        new_fields = dict(self.view.fields)
        for fill in changes.fills:
            new_fields[fill.field_path] = FieldValue(True, fill.value_json, "machine")
        new_claims = dict(self.claims)
        for held in changes.claims:
            new_claims.setdefault(held.proposal.content_hash, held)
        sections = dict(self.view.section_states)
        sections[changes.section] = changes.coverage
        # Apply the staged values together, representing the future SQL transaction.
        self.view = replace(self.view, fields=tuple(sorted(new_fields.items())), section_states=tuple(sorted(sections.items())))
        self.claims = new_claims
        receipt = CommitReceipt(lease.job.job_key, changes.fingerprint, changes.completion_state)
        self.receipts[key] = receipt
        self.state = changes.completion_state
        if self.drop_commit_response:
            raise UnknownCommit()
        return receipt

    def defer(self, lease, state, reason, not_before, now):
        self.fence(lease, now)
        self.defer_calls.append((state, reason, not_before))
        self.state = state

    def lookup_result(self, job_key, result_hash):
        return None if self.hide_receipt else self.receipts.get((job_key, result_hash))


def success(job, context):
    return PreparedWork((website_claim(),))


class WorkerTests(unittest.TestCase):
    def test_success_then_repeat_no_duplicate_and_no_processor_repeat(self):
        queue = FakeQueue()
        calls = []
        def processor(job, context):
            calls.append(job.job_key)
            return success(job, context)
        self.assertEqual(run_one(queue, processor, policy=POLICY, clock=lambda: NOW).state, "SUCCEEDED")
        self.assertEqual(run_one(queue, processor, policy=POLICY, clock=lambda: NOW).state, "IDLE")
        self.assertEqual((len(calls), len(queue.claims), queue.commit_calls), (1, 1, 1))

    def test_quota_and_source_wait_do_not_commit_or_complete_coverage(self):
        for error, state in ((QuotaUnavailable, "WAIT_QUOTA"), (SourceUnavailable, "WAIT_SOURCE")):
            queue = FakeQueue()
            def processor(job, context):
                raise error(NOW+timedelta(hours=1))
            with self.subTest(state=state):
                result = run_one(queue, processor, policy=POLICY, clock=lambda: NOW)
                self.assertEqual(result.state, state)
                self.assertEqual((queue.commit_calls, len(queue.claims)), (0, 0))
                self.assertEqual(queue.view.section_states, ())

    def test_expired_worker_cannot_save_or_commit(self):
        queue = FakeQueue()
        time = [NOW]
        def processor(job, context):
            time[0] += timedelta(seconds=301)
            return success(job, context)
        result = run_one(queue, processor, policy=POLICY, clock=lambda: time[0])
        self.assertEqual(result.state, "LEASE_LOST")
        self.assertEqual(queue.commit_calls, 0)
        self.assertEqual(queue.claims, {})

    def test_old_token_cannot_commit_after_reclaim(self):
        queue = FakeQueue()
        old = queue.claim(POLICY.fingerprint, NOW, 300)
        new = queue.claim(POLICY.fingerprint, NOW+timedelta(seconds=301), 300)
        self.assertNotEqual(old.token, new.token)
        with self.assertRaises(LeaseLost):
            queue.fence(old, NOW+timedelta(seconds=302))
        queue.fence(new, NOW+timedelta(seconds=302))

    def test_manual_edit_during_processing_stops_write(self):
        queue = FakeQueue()
        def processor(job, context):
            queue.view = replace(queue.view, fields=(("/identity/website", FieldValue(True, '"manual"', "manual")),))
            return success(job, context)
        result = run_one(queue, processor, policy=POLICY, clock=lambda: NOW)
        self.assertEqual((result.state, result.reason), ("NEEDS_REVIEW", "STALE_CONTEXT"))
        self.assertEqual(queue.commit_calls, 0)
        self.assertEqual(queue.claims, {})

    def test_unknown_commit_response_reconciled_without_second_write(self):
        queue = FakeQueue()
        queue.drop_commit_response = True
        result = run_one(queue, success, policy=POLICY, clock=lambda: NOW)
        self.assertEqual(result.state, "SUCCEEDED")
        self.assertEqual(queue.commit_calls, 1)
        self.assertEqual(len(queue.claims), 1)

    def test_unknown_commit_unresolved_no_defer_or_second_write(self):
        queue = FakeQueue()
        queue.drop_commit_response = queue.hide_receipt = True
        result = run_one(queue, success, policy=POLICY, clock=lambda: NOW)
        self.assertEqual(result.state, "COMMIT_UNKNOWN")
        self.assertEqual(queue.commit_calls, 1)
        self.assertEqual(queue.defer_calls, [])

    def test_restart_after_ambiguous_abort_reuses_prepared_result(self):
        queue = FakeQueue()
        queue.abort_before_commit = True
        calls = []
        def processor(job, context):
            calls.append(job.job_key)
            return success(job, context)
        first = run_one(queue, processor, policy=POLICY, clock=lambda: NOW)
        self.assertEqual(first.state, "COMMIT_UNKNOWN")
        self.assertIsNotNone(queue.prepared)
        self.assertEqual(queue.claims, {})
        second = run_one(queue, processor, policy=POLICY, clock=lambda: NOW+timedelta(seconds=301))
        self.assertEqual(second.state, "SUCCEEDED")
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(queue.claims), 1)

    def test_partial_extraction_does_not_complete_job(self):
        queue = FakeQueue()
        bad = replace(website_claim(), evidence=())
        result = run_one(queue, lambda *_: PreparedWork((bad,)), policy=POLICY, clock=lambda: NOW)
        self.assertEqual((result.state, result.reason), ("NEEDS_REVIEW", "NO_COMPLETE_RESULT"))
        self.assertEqual(queue.commit_calls, 0)

    def test_unknown_external_outcome_is_not_retried(self):
        queue = FakeQueue()
        calls = []
        def processor(*_):
            calls.append(True)
            raise ExternalOutcomeUnknown()
        self.assertEqual(run_one(queue, processor, policy=POLICY, clock=lambda: NOW).state, "NEEDS_REVIEW")
        self.assertEqual(run_one(queue, processor, policy=POLICY, clock=lambda: NOW).state, "IDLE")
        self.assertEqual(len(calls), 1)

    def test_valid_subset_of_partial_response_does_not_finish(self):
        queue = FakeQueue()
        result = run_one(queue, lambda *_: PreparedWork((website_claim(),), complete=False),
                         policy=POLICY, clock=lambda: NOW)
        self.assertEqual((result.state, result.reason), ("NEEDS_REVIEW", "PARTIAL_EXTRACTION"))
        self.assertEqual(queue.commit_calls, 0)
        self.assertIsNone(queue.prepared)

    def test_valid_claims_preserved_but_conflicts_keep_review_state(self):
        queue = FakeQueue()
        valid = website_claim()
        invalid = replace(valid, claim_ref="bad-source", value_json='"unmatched value"')
        result = run_one(queue, lambda *_: PreparedWork((valid, invalid)), policy=POLICY, clock=lambda: NOW)
        self.assertEqual(result.state, "NEEDS_REVIEW")
        self.assertEqual(len(queue.claims), 1)
        self.assertEqual(queue.commit_calls, 1)

    def test_unexpected_failure_sanitized_no_blind_retry(self):
        queue = FakeQueue()
        def processor(*_):
            raise RuntimeError("PRIVATE DATA MUST NOT BE LOGGED")
        result = run_one(queue, processor, policy=POLICY, clock=lambda: NOW)
        self.assertEqual((result.state, result.reason), ("FAILED", "PROCESSOR_OR_PORT_FAILURE"))
        self.assertNotIn("PRIVATE", repr(result))

    def test_protected_record_never_sent_to_processor(self):
        queue = FakeQueue(view=CompanyView(CID, protected=True))
        def processor(*_):
            self.fail("protected record should not reach processor")
        self.assertEqual(run_one(queue, processor, policy=POLICY, clock=lambda: NOW).state, "NEEDS_REVIEW")
        self.assertEqual(queue.commit_calls, 0)

    def test_no_source_adapter_claimed_implemented(self):
        job = JobSpec("DISCOVER_SOURCE", POLICY.fingerprint, POLICY.version, digest("source"), source_ref="synthetic")
        queue = FakeQueue(job=job)
        result = run_one(queue, success, policy=POLICY, clock=lambda: NOW)
        self.assertEqual(result.reason, "SOURCE_ADAPTER_NOT_IMPLEMENTED")
        self.assertEqual(queue.commit_calls, 0)

    def test_cached_prepared_result_must_match_job_section(self):
        from collectors.companies.automation.changes import evaluate
        from companies_automation.test_changes import claim
        queue = FakeQueue()
        queue.prepared = evaluate(queue.view, (claim(),), section="PRODUCTS", policy=POLICY, now=NOW)
        result = run_one(queue, success, policy=POLICY, clock=lambda: NOW)
        self.assertEqual((result.state, result.reason), ("NEEDS_REVIEW", "PREPARED_SCOPE_MISMATCH"))
        self.assertEqual(queue.commit_calls, 0)

    def test_native_and_network_modules_not_imported_by_core(self):
        for name in ("psycopg", "requests", "trafilatura", "langextract", "nomenklatura", "followthemoney", "cloud_environment"):
            self.assertNotIn(name, sys.modules)
