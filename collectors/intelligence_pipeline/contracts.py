"""Versioned, JSON-safe contracts shared by workers and future cloud adapters."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


STAGES = ("extract", "analyze", "review")
EVENT_STAGES = {"announced", "planned", "signed", "delivered", "unknown"}


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(json_text(value).encode("utf-8")).hexdigest()


def aware(value):
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(timezone.utc)


def canonical_url(value):
    if not isinstance(value, str):
        raise ValueError("HTTPS source URL required")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("HTTPS source URL without credentials required")
    # Preserve meaningful query arguments, path case and port; this is not a fetcher.
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"gclid", "fbclid"}]
    return urlunsplit(("https", parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


@dataclass(frozen=True)
class Document:
    item_id: str
    url: str
    title: str
    text: str
    published_at: str
    fetched_at: str
    source_stage: str = "unknown"
    complete: bool = False
    rights_verified: bool = False
    sensitive: bool = False
    verified_entity_ids: tuple = ()

    def __post_init__(self):
        for value in (self.item_id, self.title, self.text):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Nonempty item_id, title and source text required")
        if len(self.item_id) > 256 or len(self.title) > 2000 or len(self.url) > 8192:
            raise ValueError("Document metadata exceeds bounds")
        canonical_url(self.url)
        for value in (self.published_at, self.fetched_at):
            if not isinstance(value, str):
                raise ValueError("ISO timestamp string required")
            aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
        if self.source_stage not in EVENT_STAGES:
            raise ValueError("Invalid source stage")
        if any(type(v) is not bool for v in (self.complete, self.rights_verified, self.sensitive)):
            raise ValueError("Evidence flags must be booleans")
        if not isinstance(self.verified_entity_ids, tuple) or any(
                not isinstance(x, str) or not x for x in self.verified_entity_ids):
            raise ValueError("Trusted entity IDs must be a tuple of nonempty strings")
        if len(self.text.encode("utf-8")) > 128 * 1024:
            raise ValueError("TOO_LARGE: do not silently truncate source text")

    @property
    def text_hash(self):
        return sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def identity(self):
        # Retrieval time/item alias do not force new model calls. Evidence changes do.
        return digest({"url": canonical_url(self.url), "text_hash": self.text_hash,
                       "published_at": self.published_at, "source_stage": self.source_stage,
                       "complete": self.complete, "rights_verified": self.rights_verified,
                       "sensitive": self.sensitive, "verified_entity_ids": self.verified_entity_ids})

    def to_dict(self):
        return asdict(self)


def exact_fields(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("Unexpected response fields")


def nonempty(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Nonempty text required")
    return value


def string_list(value):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError("List of nonempty strings required")
    return tuple(value)


def validate_claims(document, payload):
    """Check literal evidence/typed consistency, NOT semantic truth or independent QA."""
    exact_fields(payload, {"document_hash", "claims"})
    if not isinstance(payload["claims"], list) or not payload["claims"]:
        raise ValueError("At least one claim required")
    reasons = []
    if payload["document_hash"] != document.text_hash:
        reasons.append("SOURCE_HASH_MISMATCH")
    if not document.complete:
        reasons.append("INCOMPLETE_SOURCE")
    if not document.rights_verified:
        reasons.append("RIGHTS_NOT_VERIFIED")
    if document.sensitive:
        reasons.append("SENSITIVE_SOURCE")
    ids = set()
    for claim in payload["claims"]:
        exact_fields(claim, {"id", "text", "quote", "stage", "entity_id", "entity_status"})
        cid, text, quote = (nonempty(claim[k]) for k in ("id", "text", "quote"))
        if cid in ids:
            reasons.append("DUPLICATE_CLAIM_ID")
        ids.add(cid)
        if quote not in document.text:
            reasons.append("QUOTE_NOT_LITERAL")
        # This catches changed literal quantities, not unit conversion or meaning.
        numbers = lambda s: set(re.findall(r"\d+(?:[.,]\d+)*", s))
        if not numbers(text) <= numbers(quote):
            reasons.append("NUMBER_NOT_IN_QUOTE")
        if claim["stage"] not in EVENT_STAGES:
            raise ValueError("Invalid claim stage")
        if claim["stage"] != document.source_stage:
            reasons.append("STAGE_MISMATCH")
        status = claim["entity_status"]
        if status not in {"verified", "candidate", "ambiguous", "not_applicable"}:
            raise ValueError("Explicit entity status required")
        if status in {"candidate", "ambiguous"}:
            reasons.append("IDENTITY_NOT_VERIFIED")
        # A model cannot grant company identity verification to its own output.
        if status == "verified" and (not isinstance(claim["entity_id"], str)
                                     or claim["entity_id"] not in document.verified_entity_ids):
            reasons.append("IDENTITY_NOT_VERIFIED")
        if status == "not_applicable" and claim["entity_id"] is not None:
            reasons.append("INVALID_ENTITY_LINK")
    return tuple(sorted(set(reasons)))


def validate_draft(payload, claims):
    exact_fields(payload, {"title", "body", "why_it_matters", "alternatives", "unknowns", "claim_ids"})
    for k in ("title", "body", "why_it_matters"):
        nonempty(payload[k])
    string_list(payload["alternatives"])
    string_list(payload["unknowns"])
    refs = string_list(payload["claim_ids"])
    if not refs or not set(refs) <= {c["id"] for c in claims}:
        raise ValueError("Draft references unavailable claims")


def validate_dossier(value, claims, context):
    """Evidence-linked reasoning, never an independent fact/forecast certification."""
    exact_fields(value, {"methods", "objects", "change", "observations", "hypotheses", "forecasts", "gaps"})
    methods = string_list(value["methods"])
    if not methods or not set(methods) <= {"IPIA", "ChangeIntelligence", "PolicyToMarket", "ACH", "Maturity"}:
        raise ValueError("METHOD_NOT_ALLOWED")
    string_list(value["objects"])
    change = value["change"]
    exact_fields(change, {"before", "after", "demand_type"})
    nonempty(change["before"]); nonempty(change["after"])
    if change["demand_type"] not in {"replacement", "upgrade", "new_capability", "unknown"}:
        raise ValueError("DEMAND_TYPE_INVALID")
    claim_ids = {c["id"] for c in claims}; context_ids = {c["id"] for c in context}
    for kind in ("observations", "hypotheses", "forecasts"):
        if not isinstance(value[kind], list) or len(value[kind]) > 10:
            raise ValueError("DOSSIER_ITEMS_INVALID")
        for row in value[kind]:
            fields = {"statement", "claim_ids", "context_refs"}
            if kind != "observations":
                fields |= {"counterhypothesis", "confirmation", "refutation", "review_at"}
            if kind == "forecasts":
                fields |= {"resolution_rule"}
            exact_fields(row, fields)  # Uncalibrated numerical probabilities disallowed.
            for key in fields - {"claim_ids", "context_refs"}:
                nonempty(row[key])
            refs = string_list(row["claim_ids"]); ctx = string_list(row["context_refs"])
            if not refs or not set(refs) <= claim_ids or not set(ctx) <= context_ids:
                raise ValueError("DOSSIER_UNAVAILABLE_EVIDENCE")
            if kind != "observations":
                aware(datetime.fromisoformat(row["review_at"].replace("Z", "+00:00")))
    if not isinstance(value["gaps"], list) or len(value["gaps"]) > 10:
        raise ValueError("DOSSIER_GAPS_INVALID")
    for gap in value["gaps"]:
        exact_fields(gap, {"question", "scope", "status"})
        nonempty(gap["question"]); nonempty(gap["scope"])
        if gap["status"] not in {"NOT_SEARCHED", "UNKNOWN_IN_SUPPLIED_SCOPE", "CONFLICT", "BLOCKED_POLICY"}:
            raise ValueError("GAP_STATUS_NOT_SUPPORTED_BY_SEARCH")
    if len(json_text(value).encode()) > 32768:
        raise ValueError("DOSSIER_TOO_LARGE")


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    compute_units: int

    def __post_init__(self):
        if any(type(x) is not int or x < 0 for x in asdict(self).values()):
            raise ValueError("Usage must contain nonnegative integers")

    def plus(self, other):
        return Usage(*(a + b for a, b in zip(asdict(self).values(), asdict(other).values())))

    def fits(self, limit):
        return all(a <= b for a, b in zip(asdict(self).values(), asdict(limit).values()))


@dataclass(frozen=True)
class ModelReply:
    payload: dict
    usage: Usage


@dataclass(frozen=True)
class JobSpec:
    document_key: str
    stage: str
    input_json: str
    pipeline_version: str

    def __post_init__(self):
        if self.stage not in STAGES or not self.pipeline_version:
            raise ValueError("Invalid stage/version")
        json.loads(self.input_json)

    @property
    def job_id(self):
        return digest([self.document_key, self.stage, digest(json.loads(self.input_json)), self.pipeline_version])


@dataclass(frozen=True)
class Lease:
    spec: JobSpec
    token: str
    attempt: int
    expires_at: datetime


@dataclass(frozen=True)
class StageResult:
    status: str
    output_json: str
    reasons: tuple = ()

    def __post_init__(self):
        if self.status not in {"DONE", "HOLD", "REJECT", "EDITORIAL_READY"}:
            raise ValueError("Invalid result status")
        json.loads(self.output_json)
