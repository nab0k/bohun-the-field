"""Cloud adapters must satisfy these atomic contracts. No implementation here calls IO."""

from datetime import datetime
from typing import Protocol

from .contracts import Document, JobSpec, Lease, ModelReply, StageResult, Usage


class LeaseLost(RuntimeError):
    pass


class QuotaExceeded(RuntimeError):
    pass


class VersionConflict(RuntimeError):
    pass


class Quota(Protocol):
    def reserve(self, request_id: str, now: datetime, estimate: Usage) -> None: ...
    def dispatch(self, request_id: str) -> None: ...
    def settle(self, request_id: str, actual: Usage) -> None: ...
    def unknown(self, request_id: str) -> None: ...


class Store(Protocol):
    def submit(self, document: Document, pipeline_version: str) -> str:
        """Atomically retain receipt and enqueue only one task for unchanged evidence."""
        ...

    def claim(self, worker: str, now: datetime, lease_seconds: int) -> Lease | None:
        """Atomically lease one due task; token must fence an expired earlier lease."""
        ...

    def finish(self, lease: Lease, now: datetime, result: StageResult,
               successor: JobSpec | None = None) -> None:
        """CAS current unexpired token; atomically store output AND enqueue successor."""
        ...

    def defer(self, lease: Lease, now: datetime, not_before: datetime, reason: str) -> None: ...


class Model(Protocol):
    def estimate(self, stage: str, payload: dict) -> Usage:
        """Full input/context + bounded output/reasoning; no implicit free request."""
        ...

    def execute(self, stage: str, payload: dict, request_id: str, maximum: Usage) -> ModelReply:
        """Never fetch URLs, run tools, retry, or choose a different provider silently."""
        ...
