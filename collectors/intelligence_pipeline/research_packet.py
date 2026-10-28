"""Bounded research inputs. No retrieval, inference, publication or persistence."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from .contracts import Document, EVENT_STAGES, aware, canonical_url, digest, json_text

MAX_TEXT_BYTES = 128 * 1024
MAX_SOURCES = 5
MAX_PACKET_BYTES = 512 * 1024


def _text(value, limit=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Nonempty bounded text required")


def _timestamp(value):
    _text(value, 64)
    return aware(datetime.fromisoformat(value.replace("Z", "+00:00"))).isoformat()


def _strings(values, maximum):
    if not isinstance(values, tuple) or len(values) > maximum:
        raise ValueError("Bounded immutable tuple required")
    for value in values:
        _text(value)
    if len(set(values)) != len(values):
        raise ValueError("Duplicate values")


@dataclass(frozen=True)
class EntityCandidate:
    label: str
    candidate_ids: tuple = ()

    def __post_init__(self):
        _text(self.label)
        _strings(self.candidate_ids, 10)

    @property
    def status(self):
        return "ambiguous" if len(self.candidate_ids) > 1 else "candidate" if self.candidate_ids else "unresolved"

    def to_dict(self):
        return {"label": self.label, "candidate_ids": sorted(self.candidate_ids), "status": self.status}


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    source_version: str
    item_id: str
    url: str
    title: str
    published_at: str
    received_at: str
    text: str | None = None
    text_hash: str | None = None
    source_stage: str = "unknown"
    completeness: str = "UNKNOWN"
    rights: str = "UNKNOWN"
    sensitive: bool = True
    entities: tuple = ()

    def __post_init__(self):
        for value in (self.source_id, self.source_version, self.item_id):
            _text(value, 256)
        _text(self.title)
        _text(self.url, 8192)
        canonical_url(self.url)
        _timestamp(self.published_at)
        _timestamp(self.received_at)
        if self.source_stage not in EVENT_STAGES:
            raise ValueError("Invalid event stage")
        if self.completeness not in {"COMPLETE", "PARTIAL", "UNKNOWN"}:
            raise ValueError("Explicit completeness required")
        if self.rights not in {"PERMITTED", "DENIED", "UNKNOWN"} or type(self.sensitive) is not bool:
            raise ValueError("Explicit rights and sensitivity required")
        if not isinstance(self.entities, tuple) or len(self.entities) > 30 or any(
                not isinstance(e, EntityCandidate) for e in self.entities):
            raise ValueError("Bounded entity candidates required")
        if self.text is None:
            if self.text_hash is not None or self.completeness == "COMPLETE":
                raise ValueError("Missing body cannot be complete or have a hash")
        else:
            _text(self.text, MAX_TEXT_BYTES)
            if len(self.text.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ValueError("TOO_LARGE: split explicitly, never truncate")
            if self.text_hash != sha256(self.text.encode("utf-8")).hexdigest():
                raise ValueError("SOURCE_HASH_MISMATCH")

    @property
    def blockers(self):
        reasons = []
        if self.sensitive:
            reasons.append("SENSITIVE_SOURCE")
        if self.rights != "PERMITTED":
            reasons.append("RIGHTS_" + self.rights)
        if self.text is None:
            reasons.append("MISSING_BODY")
        if self.completeness != "COMPLETE":
            reasons.append("COMPLETENESS_" + self.completeness)
        if any(e.status == "ambiguous" for e in self.entities):
            reasons.append("AMBIGUOUS_ENTITY")
        return tuple(reasons)

    @property
    def version_key(self):
        return self.source_id, self.source_version, canonical_url(self.url)

    def core(self):
        return {"source_id": self.source_id, "source_version": self.source_version,
                "url": canonical_url(self.url), "title": self.title, "text": self.text,
                "text_hash": self.text_hash, "published_at": _timestamp(self.published_at),
                "source_stage": self.source_stage, "completeness": self.completeness,
                "rights": self.rights, "sensitive": self.sensitive,
                "entities": sorted((e.to_dict() for e in self.entities), key=json_text)}

    @property
    def identity(self):
        return digest(self.core())

    def receipt(self):
        return {"source_key": self.identity, "item_id": self.item_id, "url": self.url,
                "received_at": _timestamp(self.received_at)}

    def to_document(self):
        if self.blockers:
            raise ValueError("Source not eligible: " + ",".join(self.blockers))
        # Candidate IDs never become trusted company identities.
        return Document(self.item_id, canonical_url(self.url), self.title, self.text,
                        _timestamp(self.published_at), _timestamp(self.received_at),
                        self.source_stage, True, True, False, ())


@dataclass(frozen=True)
class EvidenceSpan:
    source_key: str
    text_hash: str
    start: int
    end: int
    quote: str

    def validate(self, sources):
        source = next((s for s in sources if s.identity == self.source_key), None)
        if source is None or source.text is None or source.text_hash != self.text_hash:
            raise ValueError("SPAN_SOURCE_MISMATCH")
        if type(self.start) is not int or type(self.end) is not int or not (
                0 <= self.start < self.end <= len(source.text)):
            raise ValueError("SPAN_OFFSETS_INVALID")
        if source.text[self.start:self.end] != self.quote:
            raise ValueError("SPAN_QUOTE_MISMATCH")

    def to_dict(self):
        return {"source_key": self.source_key, "text_hash": self.text_hash,
                "start": self.start, "end": self.end, "quote": self.quote}


@dataclass(frozen=True)
class ResearchPacket:
    """Construct through validated records; no permissive JSON deserializer.

    Offsets refer to Python Unicode code points in the supplied stored text,
    not raw HTML/PDF bytes. Literal support is not semantic verification.
    """

    lead_key: str
    sources: tuple
    evidence: tuple
    categories: tuple
    rule_version: str
    rule_hash: str
    questions: tuple
    unknowns: tuple = ()
    previous_state: tuple = ()
    receipt_sources: tuple = ()
    external_page_limit: int = 0
    model_call_limit: int = 0

    def __post_init__(self):
        if not isinstance(self.sources, tuple) or not 1 <= len(self.sources) <= MAX_SOURCES or any(
                not isinstance(s, SourceRecord) for s in self.sources):
            raise ValueError("1–5 immutable sources required")
        if any(s.blockers for s in self.sources):
            raise ValueError("INELIGIBLE_PACKET_SOURCE")
        keys = {s.identity for s in self.sources}
        if len(keys) != len(self.sources) or len({s.version_key for s in self.sources}) != len(keys):
            raise ValueError("DUPLICATE_OR_CONFLICTING_SOURCE")
        if self.lead_key not in keys:
            raise ValueError("Lead source missing")
        if sum(len(s.text.encode("utf-8")) for s in self.sources) > MAX_TEXT_BYTES:
            raise ValueError("PACKET_TOO_LARGE: split explicitly, never truncate")
        for spans in (self.evidence, self.previous_state):
            if not isinstance(spans, tuple) or len(spans) > 50 or any(
                    not isinstance(span, EvidenceSpan) for span in spans):
                raise ValueError("Bounded immutable evidence required")
            for span in spans:
                span.validate(self.sources)
        if not self.evidence or not any(s.source_key == self.lead_key for s in self.evidence):
            raise ValueError("Literal lead evidence required")
        for values, maximum in ((self.categories, 20), (self.questions, 20), (self.unknowns, 30)):
            _strings(values, maximum)
        if not self.categories or not self.questions:
            raise ValueError("Categories and research questions required")
        _text(self.rule_version, 128)
        if not isinstance(self.rule_hash, str) or len(self.rule_hash) != 64 or any(
                c not in "0123456789abcdef" for c in self.rule_hash):
            raise ValueError("Rule configuration SHA256 required")
        if any(type(v) is not int or not 0 <= v <= cap for v, cap in (
                (self.external_page_limit, 20), (self.model_call_limit, 10))):
            raise ValueError("Invalid research limits")
        if not isinstance(self.receipt_sources, tuple) or len(self.receipt_sources) > 50 or any(
                not isinstance(s, SourceRecord) or s.identity not in keys for s in self.receipt_sources):
            raise ValueError("Receipts must refer to packet source versions")
        if len(self.to_json().encode("utf-8")) > MAX_PACKET_BYTES:
            raise ValueError("SERIALIZED_PACKET_TOO_LARGE: split explicitly, never truncate")

    def core(self):
        unknowns = set(self.unknowns)
        if not self.previous_state:
            unknowns.add("PRIOR_STATE_NOT_RETRIEVED; CHANGE_NOT_PROVEN")
        return {"contract": "exp009.research-packet.v1", "lead_key": self.lead_key,
                "sources": [{"source_key": s.identity, **s.core()}
                            for s in sorted(self.sources, key=lambda s: s.identity)],
                "evidence": sorted((s.to_dict() for s in self.evidence), key=json_text),
                "previous_state": sorted((s.to_dict() for s in self.previous_state), key=json_text),
                "history_status": "SUPPLIED_ONLY" if self.previous_state else "NOT_RETRIEVED",
                "context_status": "SUPPLIED_ONLY" if len(self.sources) > 1 else "NOT_RETRIEVED",
                "categories": sorted(self.categories), "rule_version": self.rule_version,
                "rule_hash": self.rule_hash, "questions": list(self.questions),
                "unknowns": sorted(unknowns), "execution": "DISABLED",
                "future_mode": "AUTOMATIC_WHEN_ENABLED",
                "limits": {"external_pages": self.external_page_limit, "model_calls": self.model_call_limit},
                "qa": {"decision": "HOLD", "public_release": False}}

    @property
    def identity(self):
        return digest(self.core())

    def to_dict(self):
        receipts = {json_text(s.receipt()): s.receipt() for s in self.sources + self.receipt_sources}
        return {"packet_id": self.identity, "core": self.core(),
                "receipts": [receipts[k] for k in sorted(receipts)]}

    def to_json(self):
        return json_text(self.to_dict())


def build_packet(lead, categories, rule_version, rule_hash, questions, *, context=(),
                 evidence=None, previous_state=(), unknowns=(), receipts=()):
    """Only use supplied sources. A whole-text excerpt is not a verified claim."""
    if not isinstance(lead, SourceRecord) or lead.blockers:
        raise ValueError("INELIGIBLE_LEAD")
    if not isinstance(context, tuple):
        raise ValueError("Immutable supplied context required")
    if evidence is None:
        evidence = (EvidenceSpan(lead.identity, lead.text_hash, 0, len(lead.text), lead.text),)
    return ResearchPacket(lead.identity, (lead,) + context, evidence, categories, rule_version,
                          rule_hash, questions, unknowns, previous_state, receipts)
