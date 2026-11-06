"""C3-10 offline-only, fail-closed final-version editorial gate.

No IO, model calls, database access or publication. A PASS is an offline verdict,
NOT permission to publish: the missing trusted-checker adapter and atomic
publisher compare-and-swap must be implemented and tested separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from hashlib import sha256
from ipaddress import ip_address
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit


POLICY_VERSION = "editor-policy-v0"
TYPES = frozenset({"synthesis", "signal", "analysis"})
STATUS = frozenset({"supported", "contradicted", "insufficient"})
NUMBERS = re.compile(
    r"(?<![A-Za-z0-9])(?:AU\$|US\$|A\$|C\$|€|£|\$)?"
    r"\d+(?:[.,]\d+)*(?:\s*(?:%|bn|billion|million|km|B|M))?"
    r"(?![A-Za-z0-9])", re.IGNORECASE
)
INLINE_URLS = re.compile(r"https?://[^\s<>\]\[()\"']+")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _hash(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EvidenceSpan:
    span_id: str
    text: str


@dataclass(frozen=True)
class EvidenceSource:
    source_id: str
    url: str
    root_source_id: str
    published_at: str
    spans: tuple[EvidenceSpan, ...]
    retrieval_verified: bool = False  # Trusted adapter must establish this, not an LLM.


@dataclass(frozen=True)
class EvidenceBundle:
    sources: tuple[EvidenceSource, ...]
    event_ids: tuple[str, ...] = ()

    def fingerprint(self) -> str:
        # Hash private spans but never include their text in a verdict or log.
        return _hash({"sources": [asdict(s) for s in sorted(
            self.sources, key=lambda s: s.source_id)], "event_ids": sorted(self.event_ids)})


@dataclass(frozen=True)
class PublishCandidate:
    material_id: str
    material_type: str
    version: int
    payload: Mapping[str, Any]  # Entire visible publishing payload, not text alone.
    author_model_id: str
    event_ids: tuple[str, ...] = ()
    event_fingerprint: str = ""


@dataclass(frozen=True)
class ClaimCheck:
    statement: str
    source_id: str
    span_id: str
    status: str  # supported | contradicted | insufficient


@dataclass(frozen=True)
class SemanticAssessment:
    content_hash: str
    evidence_hash: str
    policy_version: str
    checker_model_id: str
    coverage_complete: bool
    claims: tuple[ClaimCheck, ...]
    # Must be produced by a future trusted independent-model adapter.
    # Supplying an instance is not an authorization to publish.


@dataclass(frozen=True)
class PublishedStory:
    material_id: str
    event_ids: tuple[str, ...]
    event_fingerprint: str
    primary_source_urls: tuple[str, ...]
    published_at: str


@dataclass(frozen=True)
class EditorPolicy:
    version: str = POLICY_VERSION
    deterministic_recall_target: float = 0.99
    semantic_recall_target: float = 0.85
    false_block_max: float = 0.05
    good_review_max: float = 0.20
    max_payload_bytes: int = 262144


@dataclass(frozen=True)
class Reason:
    rule_id: str
    severity: str  # BLOCK or REVIEW
    code: str


@dataclass(frozen=True)
class EditorVerdict:
    decision: str  # PASS | REVIEW | BLOCK
    material_id: str
    material_type: str
    version: int
    content_hash: str
    evidence_hash: str
    policy_version: str
    checker_model_id: str
    reasons: tuple[Reason, ...] = ()
    offline_only: bool = True

    def matches(self, candidate: PublishCandidate, evidence: EvidenceBundle,
                policy: EditorPolicy) -> bool:
        """Check immutable pins only. NOT an atomic publishing permission."""
        return (self.decision == "PASS" and self.material_id == candidate.material_id
                and self.material_type == candidate.material_type
                and self.version == candidate.version
                and self.content_hash == _hash(candidate.payload)
                and self.evidence_hash == evidence.fingerprint()
                and self.policy_version == policy.version)


def _url_ok(value: Any) -> bool:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        p = urlsplit(value)
        if p.scheme != "https" or not p.hostname or p.username or p.password:
            return False
        # Reject local endpoints even though this module never fetches URLs.
        host = p.hostname.lower().rstrip(".")
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
            return False
        try:
            addr = ip_address(host)
            if not addr.is_global:
                return False
        except ValueError:
            pass
        return True
    except ValueError:
        return False


def _date(value: Any) -> date:
    if not isinstance(value, str) or not value:
        raise ValueError("date required")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timestamp offset required")
    return dt.date()


def _numbers(text: str) -> set[str]:
    return {re.sub(r"[\s,]", "", m.group()).lower() for m in NUMBERS.finditer(text)}


def _all_payload_urls(value: Any):
    """Inspect URL fields and literal links within visible payload strings."""
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            if key.lower() in {"url", "link", "canonical_url", "source_url"} or key.lower().endswith("_url"):
                yield child
            else:
                yield from _all_payload_urls(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _all_payload_urls(child)
    elif isinstance(value, str):
        for match in INLINE_URLS.finditer(value):
            yield match.group().rstrip(".,;:!?")


def _date_fields(value: Any):
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            if key.lower() in {"published_at", "event_date", "observed_at",
                               "as_of", "date", "deadline", "valid_until"}:
                yield child
            else:
                yield from _date_fields(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _date_fields(child)


def review(candidate: PublishCandidate, evidence: EvidenceBundle,
           policy: EditorPolicy | None = None,
           *, assessment: SemanticAssessment | None = None,
           published: Sequence[PublishedStory] = ()) -> EditorVerdict:
    """Deterministic preflight + externally supplied semantic assessment.

    No automatic extraction, no web probing, no source discovery and no model call.
    A caller cannot use this function as a production publishing gate.
    """
    policy = policy or EditorPolicy()
    payload_hash = _hash(candidate.payload)
    evidence_hash = evidence.fingerprint()
    reasons: list[Reason] = []

    def flag(rule: str, severity: str, code: str) -> None:
        item = Reason(rule, severity, code)
        if item not in reasons:
            reasons.append(item)

    if (not candidate.material_id or candidate.material_type not in TYPES
            or type(candidate.version) is not int or candidate.version < 1
            or not isinstance(candidate.payload, Mapping)):
        flag("E00", "BLOCK", "invalid_candidate_contract")
    if len(_canonical(candidate.payload).encode("utf-8")) > policy.max_payload_bytes:
        flag("E00", "BLOCK", "payload_too_large")
    if not isinstance(candidate.payload.get("headline"), str) or not candidate.payload["headline"].strip():
        flag("E17", "BLOCK", "missing_headline")
    if candidate.material_type == "synthesis":
        for required in ("what_happened", "why_it_matters"):
            if not isinstance(candidate.payload.get(required), str) or not candidate.payload[required].strip():
                flag("E00", "BLOCK", "missing_" + required)
    elif not isinstance(candidate.payload.get("body"), str) or not candidate.payload["body"].strip():
        flag("E00", "BLOCK", "missing_body")

    for url in _all_payload_urls(candidate.payload):
        if not _url_ok(url):
            flag("E01", "BLOCK", "invalid_payload_url")
    for value in _date_fields(candidate.payload):
        try:
            _date(value)
        except (ValueError, TypeError, OverflowError):
            flag("E07", "BLOCK", "invalid_payload_date")

    source_map: dict[str, EvidenceSource] = {}
    span_map: dict[tuple[str, str], str] = {}
    for src in evidence.sources:
        if (not src.source_id or not src.root_source_id or src.source_id in source_map
                or not _url_ok(src.url)):
            flag("E01", "BLOCK", "invalid_evidence_source")
        source_map[src.source_id] = src
        try:
            _date(src.published_at)
        except (ValueError, TypeError, OverflowError):
            flag("E07", "BLOCK", "invalid_source_date")
        if src.retrieval_verified is not True:
            flag("E01", "REVIEW", "source_not_independently_retrieved")
        for span in src.spans:
            key = (src.source_id, span.span_id)
            if not span.span_id or not span.text or key in span_map:
                flag("E03", "BLOCK", "invalid_evidence_span")
            span_map[key] = span.text
    if not source_map:
        flag("E03", "REVIEW", "no_evidence")

    refs = candidate.payload.get("sources")
    if not isinstance(refs, list) or not refs:
        flag("E01", "REVIEW", "missing_source_manifest")
    else:
        for ref in refs:
            if not isinstance(ref, Mapping) or ref.get("source_id") not in source_map:
                flag("E01", "REVIEW", "unmatched_source_manifest")
            elif ref.get("url") != source_map[ref["source_id"]].url:
                flag("E01", "BLOCK", "source_url_mismatch")

    for item in candidate.payload.get("context_items", ()):
        if not isinstance(item, Mapping):
            flag("E04", "BLOCK", "invalid_context_item")
            continue
        if (item.get("source_id"), item.get("span_id")) not in span_map:
            flag("E04", "BLOCK", "context_without_verified_span")
        linked = item.get("event_ids")
        if not isinstance(linked, list) or not linked:
            flag("E04", "REVIEW", "context_event_not_attributed")
        elif not set(linked).intersection(candidate.event_ids):
            flag("E04", "BLOCK", "unrelated_context")

    if candidate.material_type == "signal":
        roots = {s.root_source_id for s in evidence.sources if s.retrieval_verified is True}
        if len(roots) < 2:
            flag("E14", "REVIEW", "insufficient_independent_roots")
        if not candidate.payload.get("baseline"):
            flag("E13", "REVIEW", "missing_baseline")
        if not candidate.payload.get("falsifier"):
            flag("E15", "REVIEW", "missing_falsifier")

    if not candidate.event_ids:
        flag("E18", "REVIEW", "missing_event_ids")
    source_urls = {s.url for s in evidence.sources}
    for prior in published:
        if prior.material_id == candidate.material_id:
            continue  # New version of same material is NOT automatically a duplicate.
        if set(candidate.event_ids).intersection(prior.event_ids):
            flag("E18", "BLOCK", "published_event_duplicate")
        elif (candidate.event_fingerprint and
              candidate.event_fingerprint == prior.event_fingerprint):
            flag("E18", "BLOCK", "published_story_fingerprint_duplicate")
        elif source_urls.intersection(prior.primary_source_urls):
            flag("E18", "REVIEW", "possible_duplicate_shared_source")

    if assessment is None:
        flag("E12", "REVIEW", "independent_semantic_assessment_missing")
        checker_model = ""
    else:
        checker_model = assessment.checker_model_id
        if (assessment.content_hash != payload_hash or
            assessment.evidence_hash != evidence_hash or
            assessment.policy_version != policy.version):
            flag("E12", "REVIEW", "assessment_pins_stale")
        if (not checker_model or checker_model == candidate.author_model_id or
                not candidate.author_model_id):
            flag("E12", "REVIEW", "checker_not_independent")
        if not assessment.coverage_complete or not assessment.claims:
            flag("E03", "REVIEW", "semantic_coverage_incomplete")
        for claim in assessment.claims:
            if claim.status not in STATUS:
                flag("E03", "REVIEW", "unknown_claim_verdict")
                continue
            if claim.status == "contradicted":
                flag("E03", "BLOCK", "claim_contradicted")
                continue
            if claim.status == "insufficient":
                flag("E03", "REVIEW", "claim_insufficient")
                continue
            span_text = span_map.get((claim.source_id, claim.span_id))
            if not span_text:
                flag("E03", "REVIEW", "claim_span_missing")
            elif not _numbers(claim.statement).issubset(_numbers(span_text)):
                flag("E02", "BLOCK", "claim_number_or_currency_mismatch")

    decision = ("BLOCK" if any(r.severity == "BLOCK" for r in reasons)
                else "REVIEW" if reasons else "PASS")
    return EditorVerdict(decision, candidate.material_id, candidate.material_type,
                         candidate.version, payload_hash, evidence_hash, policy.version,
                         checker_model, tuple(reasons))
