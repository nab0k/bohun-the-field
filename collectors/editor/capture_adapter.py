"""C3-10 offline bridge: attach byte/hash checked captured passages to an EvidenceBundle.

Captures and quote offsets come from PRIVATE local snapshots. This module performs
only integrity and attribution checks; it does NOT independently retrieve a URL,
validate the publisher, or semantically support a claim. Therefore every returned
source is explicitly retrieval_verified=False. Do not use it to authorize a
publication or to promote an assessment to trusted status.

No imports with network access, provider SDKs, secrets, or database connections.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import re
from typing import Sequence

from .core import EvidenceBundle, EvidenceSource, EvidenceSpan

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_CAPTURE_CHARS = 2_000_000
_MAX_QUOTE_CHARS = 200


@dataclass(frozen=True)
class LocalCapture:
    """A local source snapshot; content_sha256 is an integrity pin, not provenance."""

    source_id: str
    url: str
    content: str
    content_sha256: str


@dataclass(frozen=True)
class QuoteOffset:
    """Python character offsets into a LocalCapture's *exact* content string."""

    source_id: str
    start: int
    end: int


def _source_map(evidence: EvidenceBundle) -> dict[str, EvidenceSource]:
    if not isinstance(evidence, EvidenceBundle) or not isinstance(evidence.sources, (tuple, list)):
        raise ValueError("invalid_evidence_bundle")
    result = {}
    for source in evidence.sources:
        if (not isinstance(source, EvidenceSource)
                or not isinstance(source.source_id, str) or not source.source_id
                or source.source_id in result or not isinstance(source.url, str)):
            raise ValueError("invalid_evidence_source")
        result[source.source_id] = source
    if not result:
        raise ValueError("empty_evidence_sources")
    return result


def attach_local_captures(
    evidence: EvidenceBundle,
    captures: Sequence[LocalCapture],
    quotes: Sequence[QuoteOffset],
) -> EvidenceBundle:
    """Attach exact spans to known sources, NEVER marking any source retrieved.

    All expected sources require a matching capture. A bad/missing capture, quote
    or hash fails the entire operation. Errors never include article text.
    Span IDs are deterministic for a URL, content digest, and character offset.
    """
    expected = _source_map(evidence)
    if not isinstance(captures, (tuple, list)) or not isinstance(quotes, (tuple, list)):
        raise ValueError("invalid_capture_collection")
    matched: dict[str, LocalCapture] = {}
    for cap in captures:
        if not isinstance(cap, LocalCapture) or not isinstance(cap.source_id, str):
            raise ValueError("invalid_capture")
        if cap.source_id in matched:
            raise ValueError("duplicate_capture_source")
        source = expected.get(cap.source_id)
        if source is None:
            raise ValueError("unexpected_capture_source")
        if not isinstance(cap.url, str) or cap.url != source.url:
            raise ValueError("capture_url_mismatch")
        if (not isinstance(cap.content, str) or not cap.content
                or len(cap.content) > _MAX_CAPTURE_CHARS):
            raise ValueError("invalid_capture_content")
        if (not isinstance(cap.content_sha256, str) or not _HASH.fullmatch(cap.content_sha256)
                or sha256(cap.content.encode("utf-8")).hexdigest() != cap.content_sha256):
            raise ValueError("capture_integrity_mismatch")
        matched[cap.source_id] = cap
    if matched.keys() != expected.keys():
        raise ValueError("missing_capture_source")

    found: dict[str, list[EvidenceSpan]] = {sid: [] for sid in expected}
    seen: set[tuple[str, int, int]] = set()
    for loc in quotes:
        if not isinstance(loc, QuoteOffset) or not isinstance(loc.source_id, str):
            raise ValueError("invalid_quote_offset")
        cap = matched.get(loc.source_id)
        if cap is None:
            raise ValueError("unknown_quote_source")
        if (type(loc.start) is not int or type(loc.end) is not int
                or loc.start < 0 or loc.end <= loc.start
                or loc.end > len(cap.content) or loc.end - loc.start > _MAX_QUOTE_CHARS):
            raise ValueError("invalid_quote_range")
        key = (loc.source_id, loc.start, loc.end)
        if key in seen:
            raise ValueError("duplicate_quote_offset")
        seen.add(key)
        text = cap.content[loc.start:loc.end]
        if not text.strip():
            raise ValueError("empty_quote_text")
        marker = (f"{loc.source_id}\0{cap.url}\0{cap.content_sha256}"
                  f"\0{loc.start}\0{loc.end}").encode("utf-8")
        span_id = "capture-" + sha256(marker).hexdigest()[:24]
        found[loc.source_id].append(EvidenceSpan(span_id, text))

    result = tuple(replace(source, spans=tuple(found[source.source_id]),
                           retrieval_verified=False) for source in evidence.sources)
    return EvidenceBundle(sources=result, event_ids=evidence.event_ids)
