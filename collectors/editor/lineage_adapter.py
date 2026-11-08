"""C3-10 offline event-document/capture lineage bridge.

Links an already-saved synthesis source manifest to the saved event_document ->
intelligence.items capture snapshot. This verifies local identity, URL, source date
and text integrity. It does NOT independently retrieve publisher websites,
validate P0/P1 classes, perform entailment checks, or authorize publication.
Every resulting evidence source remains retrieval_verified=False.

No database/network/LLM imports, secrets, filesystem access, or side effects.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

from .capture_adapter import LocalCapture, QuoteOffset, attach_local_captures
from .core import EvidenceBundle, PublishCandidate


@dataclass(frozen=True)
class SourceItemBinding:
    """A manifest source_id explicitly mapped to an intelligence.items row id."""

    source_id: str
    item_id: int


@dataclass(frozen=True)
class CapturedItem:
    """Read-only intelligence.items snapshot; hash comes from an external export."""

    item_id: int
    url: str
    published_at: str
    content: str  # Exact saved content_capture.text; never log this field.
    content_sha256: str


@dataclass(frozen=True)
class EventDocumentLink:
    """Read-only knowledge.event_document row, without trust or class inference."""

    event_id: int
    item_id: int


def _day(value: str) -> date:
    """Parse source dates, rejecting datetime values without a timezone."""
    if not isinstance(value, str) or not value:
        raise ValueError("invalid_linked_publication_date")
    try:
        if len(value) == 10:
            return date.fromisoformat(value)
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("timezone_required")
        return moment.date()
    except ValueError:
        raise ValueError("invalid_linked_publication_date") from None


def bind_event_document_captures(
    candidate: PublishCandidate,
    evidence: EvidenceBundle,
    source_bindings: Sequence[SourceItemBinding],
    items: Sequence[CapturedItem],
    links: Sequence[EventDocumentLink],
    quotes: Sequence[QuoteOffset],
) -> EvidenceBundle:
    """Return span-bearing but **untrusted** evidence with strict event lineage.

    No implicit URL matching, event guessing or source reclassification:
    every manifest source must have exactly one link to one existing item.
    A missing/extra/duplicate relation, different URL/date or bad capture
    fails without including article text in an error message.
    """
    if not isinstance(candidate, PublishCandidate) or not isinstance(evidence, EvidenceBundle):
        raise ValueError("invalid_lineage_contract")
    if (not isinstance(candidate.event_ids, (list, tuple)) or not candidate.event_ids
            or not isinstance(evidence.event_ids, (list, tuple)) or not evidence.event_ids):
        raise ValueError("missing_lineage_event")
    if not isinstance(evidence.sources, (list, tuple)) or not evidence.sources:
        raise ValueError("missing_lineage_sources")
    if not all(isinstance(s.source_id, str) and s.source_id for s in evidence.sources):
        raise ValueError("invalid_lineage_source_id")
    expected = {s.source_id: s for s in evidence.sources}
    if len(expected) != len(evidence.sources):
        raise ValueError("duplicate_lineage_source")
    if not all(isinstance(t, (tuple, list)) for t in (source_bindings, items, links, quotes)):
        raise ValueError("invalid_lineage_collection")

    bindings: dict[str, int] = {}
    for bound in source_bindings:
        if (not isinstance(bound, SourceItemBinding) or not isinstance(bound.source_id, str)
                or bound.source_id not in expected or type(bound.item_id) is not int
                or bound.item_id < 1):
            raise ValueError("invalid_source_item_binding")
        if bound.source_id in bindings:
            raise ValueError("duplicate_source_item_binding")
        bindings[bound.source_id] = bound.item_id
    if bindings.keys() != expected.keys() or len(set(bindings.values())) != len(bindings):
        raise ValueError("incomplete_source_item_binding")

    by_item: dict[int, CapturedItem] = {}
    for item in items:
        if not isinstance(item, CapturedItem) or type(item.item_id) is not int or item.item_id < 1:
            raise ValueError("invalid_linked_item")
        if item.item_id in by_item:
            raise ValueError("duplicate_linked_item")
        by_item[item.item_id] = item
    if by_item.keys() != set(bindings.values()):
        raise ValueError("incomplete_linked_items")

    relations: set[tuple[int, int]] = set()
    for link in links:
        if (not isinstance(link, EventDocumentLink) or type(link.event_id) is not int
                or link.event_id < 1 or type(link.item_id) is not int or link.item_id < 1):
            raise ValueError("invalid_event_document_link")
        key = (link.event_id, link.item_id)
        if key in relations:
            raise ValueError("duplicate_event_document_link")
        relations.add(key)

    captures = []
    for source_id, item_id in bindings.items():
        source, item = expected[source_id], by_item[item_id]
        if item.url != source.url:
            raise ValueError("linked_item_url_mismatch")
        if _day(item.published_at) != _day(source.published_at):
            raise ValueError("linked_item_publication_date_mismatch")
        if not any(
            (f"event-{event_id}" in candidate.event_ids
             and f"event-{event_id}" in evidence.event_ids)
            for event_id, linked_item_id in relations if linked_item_id == item_id
        ):
            raise ValueError("item_not_linked_to_candidate_event")
        captures.append(LocalCapture(source_id, item.url, item.content, item.content_sha256))
    if len(relations) != len(bindings):
        raise ValueError("unexpected_event_document_link")
    return attach_local_captures(evidence, captures, quotes)
