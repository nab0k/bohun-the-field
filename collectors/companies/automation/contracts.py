"""Immutable, JSON-safe contracts. Source/identity receipts come from trusted ports.

These checks establish structural consistency, not truth or semantic extraction.
Live admission, source transport, native extraction and SQL writer are later ports.
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import re
from urllib.parse import urlsplit


SECTIONS = ("IDENTITY", "CLASSIFICATION", "PRODUCTS", "TECHNOLOGIES", "PROGRAMMES",
            "EVENTS", "RELATIONSHIPS", "FINANCIALS", "GEOGRAPHY", "SOURCES")
STATES = ("READY", "LEASED", "SUCCEEDED", "WAIT_SOURCE", "WAIT_QUOTA",
          "NEEDS_REVIEW", "FAILED", "CANCELLED")
COVERAGE = ("NOT_RESEARCHED", "RESEARCHED_NOT_FOUND", "NOT_APPLICABLE",
            "HAS_HOLD_CLAIMS", "HAS_APPROVED_CLAIMS")
ASSESSMENTS = ("registry_supported", "cross_source", "company_claim", "inferred_needs_review")
INTERVAL_DAYS = {s: (7 if s in ("EVENTS", "PROGRAMMES") else
                     90 if s in ("IDENTITY", "FINANCIALS", "GEOGRAPHY", "SOURCES") else 30)
                 for s in SECTIONS}
SOURCE_KINDS = ("official_registry", "official_award", "official_company", "news", "saved_binding")
BINDINGS = ("identifier", "official_name_country_domain", "saved_binding")
MODALITIES = ("actual", "planned", "possible", "denied")
EVENT_KINDS = ("none", "notice", "award", "contract", "delivery", "grant", "investment",
               "ownership", "parent", "supplier", "ubo")


class ContractError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise ContractError(code)


def aware(value):
    require(isinstance(value, datetime) and value.utcoffset() is not None, "AWARE_TIME_REQUIRED")
    return value.astimezone(timezone.utc)


def json_text(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        raise ContractError("JSON_VALUE_INVALID") from None


def parse_json(value):
    try:
        parsed = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError):
        raise ContractError("JSON_VALUE_INVALID") from None
    require(json_text(parsed) == value, "CANONICAL_JSON_REQUIRED")
    return parsed


def digest(value):
    return hashlib.sha256(json_text(value).encode("utf-8")).hexdigest()


def is_hash(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def identifier(value, label="ID"):
    require(isinstance(value, str) and 0 < len(value) <= 256 and not any(ord(c) < 32 for c in value), label)
    return value


def https_host(url):
    """Syntax/IP-literal guard only. DNS/robots/redirect admission belongs to transport."""
    try:
        p = urlsplit(url)
        host = p.hostname
        require(p.scheme == "https" and host and not p.username and not p.password
                and not p.fragment and p.port in (None, 443), "HTTPS_URL_REQUIRED")
        require(not any(c.isspace() or ord(c) < 32 for c in url), "URL_INVALID")
        host = host.lower().rstrip(".")
        require(host != "localhost" and not host.endswith(".localhost") and "." in host, "LOCAL_HOST_DENIED")
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        require(ip is None or ip.is_global, "PRIVATE_IP_DENIED")
        return host
    except (TypeError, ValueError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("URL_INVALID") from None


@dataclass(frozen=True)
class Policy:
    version: str = "continuous_v0"
    fill_paths: tuple = ("/identity/website", "/identity/country")
    max_claims: int = 100
    lease_seconds: int = 300
    max_attempts: int = 3

    def __post_init__(self):
        require(bool(re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", self.version)), "POLICY_VERSION_INVALID")
        require(type(self.fill_paths) is tuple and len(set(self.fill_paths)) == len(self.fill_paths)
                and set(self.fill_paths) <= {"/identity/website", "/identity/country"}, "FILL_ALLOWLIST_INVALID")
        require(type(self.max_claims) is int and 1 <= self.max_claims <= 1000, "CLAIM_LIMIT_INVALID")
        require(type(self.lease_seconds) is int and 1 <= self.lease_seconds <= 300, "LEASE_LIMIT_INVALID")
        require(type(self.max_attempts) is int and 1 <= self.max_attempts <= 10, "ATTEMPT_LIMIT_INVALID")

    @property
    def fingerprint(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class IdentityEvidence:
    source_url: str
    source_hash: str
    source_kind: str
    entity_kind: str
    industry_relevant: bool
    observed_name: str
    observed_country: str | None
    document_identifiers: tuple = ()
    legal_entity_schemes: tuple = ()
    bound_page_id: str | None = None
    expected_role: str = "company"
    observed_role: str = "company"

    def __post_init__(self):
        https_host(self.source_url)
        require(is_hash(self.source_hash) and self.source_kind in SOURCE_KINDS, "IDENTITY_RECEIPT_INVALID")
        require(type(self.industry_relevant) is bool, "RELEVANCE_REQUIRED")
        identifier(self.observed_name, "OBSERVED_NAME_REQUIRED")
        require(type(self.document_identifiers) is tuple and type(self.legal_entity_schemes) is tuple,
                "IMMUTABLE_IDENTIFIERS_REQUIRED")
        for pair in self.document_identifiers:
            require(type(pair) is tuple and len(pair) == 2 and all(isinstance(x, str) and x for x in pair),
                    "IDENTIFIER_PAIR_INVALID")


@dataclass(frozen=True)
class CompanySnapshot:
    company_id: str
    version: str
    status: str = "candidate"
    identity_ready: bool = True
    manual_hold: bool = False

    def __post_init__(self):
        identifier(self.company_id)
        require(is_hash(self.version), "INPUT_HASH_REQUIRED")
        require(self.status in ("candidate", "card", "dossier", "excluded"), "COMPANY_STATUS_INVALID")
        require(type(self.identity_ready) is bool and type(self.manual_hold) is bool, "BOOL_REQUIRED")


@dataclass(frozen=True)
class JobSpec:
    kind: str
    policy_hash: str
    policy_version: str
    input_hash: str
    company_id: str | None = None
    section: str | None = None
    source_ref: str | None = None
    revision: str = "initial"
    initial_state: str = "READY"
    reason: str | None = None

    def __post_init__(self):
        require(self.kind in ("ENRICH_SECTION", "DISCOVER_SOURCE"), "CORE_JOB_KIND_INVALID")
        require(is_hash(self.policy_hash) and is_hash(self.input_hash), "JOB_HASH_REQUIRED")
        identifier(self.policy_version)
        identifier(self.revision)
        require(self.initial_state in ("READY", "NEEDS_REVIEW", "CANCELLED"), "INITIAL_STATE_INVALID")
        if self.kind == "ENRICH_SECTION":
            identifier(self.company_id)
            require(self.section in SECTIONS and self.source_ref is None, "SECTION_SCOPE_REQUIRED")
        else:
            identifier(self.source_ref)
            require(self.company_id is None and self.section is None, "SOURCE_SCOPE_REQUIRED")

    @property
    def queue(self):
        return "enrichment" if self.kind == "ENRICH_SECTION" else "discovery"

    @property
    def job_key(self):
        return digest([self.policy_hash, self.policy_version, self.kind, self.company_id,
                       self.section, self.source_ref, self.input_hash, self.revision])


@dataclass(frozen=True)
class SectionState:
    coverage: str = "NOT_RESEARCHED"
    next_due: datetime | None = None
    input_hash: str | None = None

    def __post_init__(self):
        require(self.coverage in COVERAGE, "COVERAGE_INVALID")
        if self.next_due is not None:
            aware(self.next_due)
            require(is_hash(self.input_hash), "CHECKED_INPUT_HASH_REQUIRED")


@dataclass(frozen=True)
class FieldValue:
    known: bool
    value_json: str = "null"
    owner: str = "unknown"

    def __post_init__(self):
        require(type(self.known) is bool and self.owner in ("unknown", "machine", "manual"), "FIELD_STATE_INVALID")
        parse_json(self.value_json)
        require(self.known or self.value_json == "null", "UNKNOWN_VALUE_PRESENT")


@dataclass(frozen=True)
class CompanyView:
    company_id: str
    country: str | None = None
    fields: tuple = ()                  # tuple[(JSON pointer, FieldValue)]
    rejected_hashes: tuple = ()
    section_states: tuple = ()          # tuple[(section, coverage)]
    protected: bool = False

    def __post_init__(self):
        identifier(self.company_id)
        require(self.country is None or bool(re.fullmatch(r"[A-Z]{2}", self.country)), "COUNTRY_FORMAT_INVALID")
        require(type(self.fields) is tuple and type(self.rejected_hashes) is tuple
                and type(self.section_states) is tuple and type(self.protected) is bool, "VIEW_INVALID")
        require(len(dict(self.fields)) == len(self.fields) and all(isinstance(v, FieldValue) for _, v in self.fields),
                "FIELD_DUPLICATE_OR_INVALID")
        require(all(s in SECTIONS and v in COVERAGE for s, v in self.section_states), "COVERAGE_INVALID")
        require(len(dict(self.section_states)) == len(self.section_states), "DUPLICATE_SECTION_STATE")
        require(all(is_hash(h) for h in self.rejected_hashes), "REJECTED_HASH_INVALID")

    @property
    def fingerprint(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class SourceDocument:
    source_ref: str
    url: str
    text: str
    captured_at: datetime
    kind: str
    independence_group: str = ""

    def __post_init__(self):
        identifier(self.source_ref)
        https_host(self.url)
        aware(self.captured_at)
        require(self.kind in SOURCE_KINDS and isinstance(self.text, str)
                and 0 < len(self.text.encode("utf-8")) <= 2 * 1024 * 1024, "SOURCE_INVALID")

    @property
    def content_hash(self):
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Qualifiers:
    polarity: str = "positive"
    modality: str = "actual"
    event_kind: str = "none"
    as_of: str | None = None
    currency: str | None = None
    unit: str | None = None
    period: str | None = None

    def __post_init__(self):
        require(self.polarity in ("positive", "negative") and self.modality in MODALITIES
                and self.event_kind in EVENT_KINDS, "QUALIFIER_INVALID")
        if self.as_of is not None:
            try:
                from datetime import date
                require(date.fromisoformat(self.as_of).isoformat() == self.as_of, "DATE_INVALID")
            except (TypeError, ValueError):
                raise ContractError("DATE_INVALID") from None
        if self.currency is not None:
            require(bool(re.fullmatch(r"[A-Z]{3}", self.currency)), "CURRENCY_INVALID")


@dataclass(frozen=True)
class Evidence:
    source: SourceDocument
    source_hash: str
    company_id: str
    binding: str
    start: int
    end: int
    quote: str
    observed: Qualifiers = field(default_factory=Qualifiers)
    observed_field_path: str | None = None
    observed_value_json: str | None = None

    def __post_init__(self):
        require(isinstance(self.source, SourceDocument) and is_hash(self.source_hash), "EVIDENCE_SOURCE_REQUIRED")
        identifier(self.company_id)
        require(self.binding in BINDINGS and isinstance(self.observed, Qualifiers), "BINDING_REQUIRED")
        require(type(self.start) is int and type(self.end) is int and isinstance(self.quote, str), "SPAN_INVALID")
        if self.observed_value_json is not None:
            parse_json(self.observed_value_json)


@dataclass(frozen=True)
class ProposedClaim:
    company_id: str
    section: str
    claim_ref: str
    field_path: str
    value_json: str
    evidence: tuple
    qualifiers: Qualifiers = field(default_factory=Qualifiers)

    def __post_init__(self):
        identifier(self.company_id)
        require(self.section in SECTIONS and 1 <= len(self.claim_ref) <= 100, "CLAIM_SCOPE_INVALID")
        require(bool(re.fullmatch(r"/(?:[a-z][a-z0-9_]*)(?:/[a-z0-9_]+)*", self.field_path))
                and len(self.field_path) < 256, "FIELD_PATH_INVALID")
        parse_json(self.value_json)
        require(type(self.evidence) is tuple and len(self.evidence) <= 10
                and all(isinstance(e, Evidence) for e in self.evidence), "EVIDENCE_INVALID")
        require(isinstance(self.qualifiers, Qualifiers), "QUALIFIER_INVALID")

    @property
    def content_hash(self):
        # Stable across ref aliases, receipt timestamps and URLs: cannot evade rejection by renaming.
        return digest([self.company_id, self.section, self.field_path, self.value_json, asdict(self.qualifiers)])


@dataclass(frozen=True)
class ResearchReceipt:
    company_id: str
    section: str
    scope: str
    complete: bool
    no_results: bool
    checked: tuple

    def __post_init__(self):
        identifier(self.company_id)
        identifier(self.scope)
        require(self.section in SECTIONS and type(self.complete) is bool and type(self.no_results) is bool
                and type(self.checked) is tuple and all(isinstance(s, SourceDocument) for s in self.checked),
                "RESEARCH_RECEIPT_INVALID")


@dataclass(frozen=True)
class Lease:
    job: JobSpec
    token: str
    expires_at: datetime
    attempt: int

    def __post_init__(self):
        require(isinstance(self.job, JobSpec), "JOB_REQUIRED")
        identifier(self.token)
        aware(self.expires_at)
        require(type(self.attempt) is int and self.attempt >= 1, "ATTEMPT_INVALID")
