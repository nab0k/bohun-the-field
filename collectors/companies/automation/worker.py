"""One-job orchestration through ports; no connection, HTTP, model or native code.

The core enrichment worker is usable with a fake queue. Discovery source fetching
and its transactional writer are not implemented in this offline stage.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Callable

from .changes import ChangeSet, evaluate, check_preconditions
from .contracts import (CompanyView, ContractError, Lease, Policy, ResearchReceipt,
                        aware, is_hash, require)


class LeaseLost(Exception):
    pass


class UnknownCommit(Exception):
    pass


class Waiting(Exception):
    state = "WAIT_SOURCE"

    def __init__(self, retry_at):
        self.retry_at = aware(retry_at)


class SourceUnavailable(Waiting):
    pass


class QuotaUnavailable(Waiting):
    state = "WAIT_QUOTA"


class ExternalOutcomeUnknown(Exception):
    """No automatic repeat of a dispatched request with an uncertain outcome."""


@dataclass(frozen=True)
class WorkContext:
    view: CompanyView
    input_hash: str

    def __post_init__(self):
        require(isinstance(self.view, CompanyView) and is_hash(self.input_hash), "CONTEXT_INVALID")


@dataclass(frozen=True)
class PreparedWork:
    proposals: tuple
    research: ResearchReceipt | None = None
    complete: bool = True

    def __post_init__(self):
        require(type(self.proposals) is tuple and type(self.complete) is bool, "IMMUTABLE_PROPOSALS_REQUIRED")


@dataclass(frozen=True)
class CommitReceipt:
    job_key: str
    result_hash: str
    state: str

    def __post_init__(self):
        require(is_hash(self.job_key) and is_hash(self.result_hash)
                and self.state in ("SUCCEEDED", "NEEDS_REVIEW"), "RECEIPT_INVALID")


@dataclass(frozen=True)
class WorkerResult:
    state: str
    job_key: str | None = None
    result_hash: str | None = None
    reason: str | None = None


class QueuePort(Protocol):
    """All mutating methods fence token/expiry under the same transaction lock.

    commit_result also validates policy, current base fingerprint and private QA,
    writes complete evidence/claims/coverage, and finishes the job atomically.
    It must return the previously committed receipt on an exact job/result replay.
    """
    def claim(self, policy_hash: str, now: datetime, lease_seconds: int) -> Lease | None: ...
    def context(self, lease: Lease) -> WorkContext: ...
    def prepared_result(self, lease: Lease) -> ChangeSet | None: ...
    def save_prepared(self, lease: Lease, changes: ChangeSet, now: datetime) -> None: ...
    def commit_result(self, lease: Lease, changes: ChangeSet, now: datetime) -> CommitReceipt: ...
    def defer(self, lease: Lease, state: str, reason: str, not_before: datetime | None, now: datetime) -> None: ...
    def lookup_result(self, job_key: str, result_hash: str) -> CommitReceipt | None: ...


def check_lease(lease, *, token, now, policy):
    require(isinstance(lease, Lease), "LEASE_REQUIRED")
    if lease.token != token or aware(lease.expires_at) <= aware(now):
        raise LeaseLost()
    require(lease.job.policy_hash == policy.fingerprint and lease.job.policy_version == policy.version,
            "POLICY_CHANGED")


def run_one(queue: QueuePort, processor: Callable, *, policy: Policy, clock: Callable):
    require(isinstance(policy, Policy), "POLICY_REQUIRED")
    now = aware(clock())
    lease = queue.claim(policy.fingerprint, now, policy.lease_seconds)
    if lease is None:
        return WorkerResult("IDLE")
    key = lease.job.job_key

    def active():
        current = aware(clock())
        check_lease(lease, token=lease.token, now=current, policy=policy)
        return current

    def defer(state, reason, not_before=None):
        queue.defer(lease, state, reason, not_before, active())
        return WorkerResult(state, key, reason=reason)

    try:
        active()
        if lease.attempt > policy.max_attempts:
            return defer("FAILED", "ATTEMPT_LIMIT")
        if lease.job.kind != "ENRICH_SECTION":
            return defer("NEEDS_REVIEW", "SOURCE_ADAPTER_NOT_IMPLEMENTED")
        context = queue.context(lease)
        require(isinstance(context, WorkContext) and context.input_hash == lease.job.input_hash
                and context.view.company_id == lease.job.company_id, "INPUT_VERSION_OR_SCOPE_CHANGED")
        if context.view.protected:
            return defer("NEEDS_REVIEW", "RECORD_PROTECTED")
        changes = queue.prepared_result(lease)
        if changes is None:
            prepared = processor(lease.job, context)
            require(isinstance(prepared, PreparedWork), "PREPARED_WORK_REQUIRED")
            if not prepared.complete:
                return defer("NEEDS_REVIEW", "PARTIAL_EXTRACTION")
            changes = evaluate(context.view, prepared.proposals, section=lease.job.section,
                               policy=policy, now=active(), research=prepared.research)
            if changes.reviewed_at is not None:
                queue.save_prepared(lease, changes, active())
        else:
            check_preconditions(changes, context.view, policy)
        require(changes.company_id == lease.job.company_id and changes.section == lease.job.section,
                "PREPARED_SCOPE_MISMATCH")
        # An invalid/partial extraction does not mark this section researched.
        if changes.reviewed_at is None:
            return defer("NEEDS_REVIEW", "NO_COMPLETE_RESULT")
        expected_hash = changes.fingerprint
        try:
            receipt = queue.commit_result(lease, changes, active())
        except UnknownCommit:
            receipt = queue.lookup_result(key, expected_hash)
            if receipt is None:
                # Keep durable ambiguity for reconciliation; no defer/second commit.
                return WorkerResult("COMMIT_UNKNOWN", key, expected_hash, "RECONCILIATION_REQUIRED")
        if not (isinstance(receipt, CommitReceipt) and receipt.job_key == key
                and receipt.result_hash == expected_hash and receipt.state == changes.completion_state):
            return WorkerResult("COMMIT_UNKNOWN", key, expected_hash, "COMMIT_RECEIPT_MISMATCH")
        return WorkerResult(receipt.state, key, expected_hash)
    except LeaseLost:
        return WorkerResult("LEASE_LOST", key, reason="STALE_WORKER_NO_COMMIT")
    except Waiting as exc:
        require(exc.retry_at > aware(clock()), "FUTURE_RETRY_REQUIRED")
        try:
            return defer(exc.state, exc.state, exc.retry_at)
        except LeaseLost:
            return WorkerResult("LEASE_LOST", key)
    except ExternalOutcomeUnknown:
        try:
            return defer("NEEDS_REVIEW", "EXTERNAL_OUTCOME_UNKNOWN")
        except LeaseLost:
            return WorkerResult("LEASE_LOST", key)
    except ContractError as exc:
        try:
            return defer("NEEDS_REVIEW", str(exc))
        except (LeaseLost, ContractError):
            return WorkerResult("LEASE_LOST", key, reason="POLICY_OR_LEASE_CHANGED")
    except Exception:
        # Sanitized, terminal: unknown failure may have made an external request.
        try:
            return defer("FAILED", "PROCESSOR_OR_PORT_FAILURE")
        except Exception:
            return WorkerResult("COMMIT_UNKNOWN", key, reason="PORT_FAILURE_RECONCILE")
