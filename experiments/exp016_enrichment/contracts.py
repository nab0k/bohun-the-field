"""Local-only contracts. No transport, credentials or production writer."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import hashlib
import json
import time

POLICY = 'EXP016-enrichment-v0.1'
SECTIONS = ('identity', 'classification', 'products', 'technologies',
            'programmes_contracts', 'events', 'relationships', 'financials', 'geography', 'sources')
SOURCE_TYPES = {'registry_record', 'company_self_claim', 'official_programme_record',
                'third_party_report', 'inference_candidate'}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def iso(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.utcoffset() is None:
        raise ValueError('TIMEZONE_REQUIRED')
    return dt


@dataclass(frozen=True)
class RunManifest:
    run_id: str
    code_hashes: dict
    dependency_hash: str
    baseline_hash: str
    baseline_checked_at: dict
    completeness: dict
    fixed_existing: tuple
    source_allowances: tuple
    deadline: str
    quotas: dict = field(default_factory=lambda: {'grants_tenders': 5, 'news': 5})
    policy: str = POLICY
    mode: str = 'offline'
    synthetic: bool = True

    def __post_init__(self):
        if self.mode not in {'offline', 'replay', 'native-fixture'} or not self.synthetic:
            raise ValueError('FIRST_STAGE_SYNTHETIC_ONLY')
        iso(self.deadline)
        if len(set(self.fixed_existing)) != len(self.fixed_existing):
            raise ValueError('DUPLICATE_FIXED_COHORT')


@dataclass(frozen=True)
class ResearchTask:
    subject: str
    section: str
    field: str
    reason: str
    allowed_sources: tuple
    queue: str = 'existing'
    status: str = 'PENDING'
    budget: int = 1


@dataclass(frozen=True)
class SourceReceipt:
    source_id: str
    url: str
    publisher: str
    dataset: str
    received_at: str
    effective_at: str
    original_sha256: str
    text_sha256: str
    format: str
    version: str
    rights_reference: str

    @property
    def key(self):
        # Retrieval timestamp alone is not new evidence.
        d = asdict(self)
        d.pop('received_at')
        return digest(d)


@dataclass(frozen=True)
class EvidenceObservation:
    subject: str
    section: str
    field: str
    value: object
    source_type: str
    receipt_key: str
    locator: dict
    subject_identifiers: tuple
    modality: str = 'asserted'
    units: str | None = None
    currency: str | None = None
    period: str | None = None
    version: str = POLICY

    @property
    def key(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class ChangeProposal:
    subject: str
    section: str
    field: str
    baseline: object
    current: object
    incoming: object
    expected_old_hash: str
    operation: str
    evidence_ids: tuple
    status: str
    reasons: tuple

    @property
    def key(self):
        return digest(asdict(self))


@dataclass
class RunResult:
    run_id: str
    mode: str
    status: str
    synthetic: bool
    before: dict
    after: dict
    observations: list
    changes: list
    unknowns: list
    holds: list
    counts: dict
    baseline_coverage: dict
    actual_seconds: float
    paid_cost: int = 0
    live_http_attempts: int = 0
    model_transport_attempts: int = 0
    native_calls: dict = field(default_factory=dict)
    limitations: list = field(default_factory=list)


class LimitReached(ValueError):
    pass


class Budget:
    """Persisted replay counters. Live/model transport is deliberately inadmissible."""
    def __init__(self, deadline, state=None, *, clock=time.monotonic):
        self.deadline = iso(deadline)
        self.clock = clock
        self.started = clock()
        self.state = dict(state or {'document_keys': [], 'bytes': 0, 'seconds': 0,
                                    'http_attempts': 0, 'model_attempts': 0})

    def check(self):
        if datetime.now(self.deadline.tzinfo) >= self.deadline:
            raise LimitReached('DEADLINE')
        if self.state['seconds'] + self.clock() - self.started >= 7200:
            raise LimitReached('WALL_LIMIT')

    def admit_document(self, key, raw_size, text_size, kind):
        self.check()
        raw_cap = 2 * 1024**2 if kind == 'html' else 5 * 1024**2
        if raw_size > raw_cap or text_size > 128 * 1024:
            raise LimitReached('DOCUMENT_BYTES')
        if key in self.state['document_keys']:
            return
        if len(self.state['document_keys']) >= 200:
            raise LimitReached('DOCUMENT_LIMIT')
        if self.state['bytes'] + raw_size + text_size > 128 * 1024**2:
            raise LimitReached('RUN_BYTES')
        self.state['document_keys'].append(key)
        self.state['bytes'] += raw_size + text_size

    def live_attempt(self, kind):
        raise LimitReached('LIVE_MODEL_NOT_ADMITTED' if kind == 'model' else 'LIVE_HTTP_NOT_ADMITTED')

    def snapshot(self):
        state = dict(self.state)
        state['seconds'] += self.clock() - self.started
        return state
