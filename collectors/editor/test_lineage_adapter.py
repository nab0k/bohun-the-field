"""Synthetic, strictly offline tests for the event-document linkage boundary."""
import unittest
from dataclasses import replace
from hashlib import sha256

from collectors.editor.capture_adapter import QuoteOffset
from collectors.editor.core import EvidenceBundle, EvidenceSource, PublishCandidate, review
from collectors.editor.lineage_adapter import (
    CapturedItem, EventDocumentLink, SourceItemBinding, bind_event_document_captures,
)

URL = "https://example.org/notice"
TEXT = "A public contract notice with 400 units. Delivery has not occurred."


def snapshot():
    source = EvidenceSource("S1", URL, "example.org", "2026-11-06", (), False)
    evidence = EvidenceBundle((source,), ("event-91",))
    candidate = PublishCandidate(
        "syn-42", "synthesis", 1,
        {"headline": "Contract notice", "what_happened": "A contract notice was issued.",
         "why_it_matters": "Potential impact on supply.", "as_of": "2026-11-07",
         "sources": [{"source_id": "S1", "url": URL}]},
        "writer-a", ("event-91",), "",
    )
    bound = [SourceItemBinding("S1", 11)]
    items = [CapturedItem(11, URL, "2026-11-06T08:15:00Z", TEXT,
                          sha256(TEXT.encode("utf-8")).hexdigest())]
    links = [EventDocumentLink(91, 11)]
    quotes = [QuoteOffset("S1", 0, 28)]
    return candidate, evidence, bound, items, links, quotes


def invoke(snapshot_parts):
    return bind_event_document_captures(*snapshot_parts)


class LineageAdapterTests(unittest.TestCase):
    def test_valid_lineage_attaches_exact_untrusted_span(self):
        candidate, evidence, bindings, items, links, quotes = snapshot()
        result = invoke((candidate, evidence, bindings, items, links, quotes))
        self.assertEqual(result.sources[0].spans[0].text, TEXT[:28])
        self.assertEqual(result.sources[0].retrieval_verified, False)
        self.assertEqual(result.event_ids, ("event-91",))
        self.assertEqual(result, invoke(snapshot()))

    def test_no_independent_checker_still_review(self):
        candidate, evidence, bindings, items, links, quotes = snapshot()
        result = review(candidate, bind_event_document_captures(candidate, evidence,
                        bindings, items, links, quotes), assessment=None)
        self.assertEqual(result.decision, "REVIEW")
        self.assertTrue(result.offline_only)

    def test_forged_verified_flag_cannot_survive(self):
        candidate, evidence, bindings, items, links, quotes = snapshot()
        forged = replace(evidence, sources=(replace(evidence.sources[0], retrieval_verified=True),))
        result = bind_event_document_captures(candidate, forged, bindings, items, links, quotes)
        self.assertIs(result.sources[0].retrieval_verified, False)

    def test_wrong_event_relation_fails(self):
        parts = list(snapshot())
        parts[4] = [EventDocumentLink(92, 11)]
        with self.assertRaisesRegex(ValueError, "item_not_linked_to_candidate_event"):
            invoke(parts)

    def test_wrong_evidence_event_fails(self):
        parts = list(snapshot())
        parts[1] = replace(parts[1], event_ids=("event-99",))
        with self.assertRaisesRegex(ValueError, "item_not_linked_to_candidate_event"):
            invoke(parts)

    def test_wrong_linked_item_url_fails(self):
        parts = list(snapshot())
        parts[3] = [replace(parts[3][0], url="https://other.org/notice")]
        with self.assertRaisesRegex(ValueError, "linked_item_url_mismatch"):
            invoke(parts)

    def test_linked_date_must_match(self):
        parts = list(snapshot())
        parts[3] = [replace(parts[3][0], published_at="2026-11-05")]
        with self.assertRaisesRegex(ValueError, "linked_item_publication_date_mismatch"):
            invoke(parts)

    def test_naive_datetime_rejected(self):
        parts = list(snapshot())
        parts[3] = [replace(parts[3][0], published_at="2026-11-06T12:34:56")]
        with self.assertRaisesRegex(ValueError, "invalid_linked_publication_date"):
            invoke(parts)

    def test_changed_capture_body_fails(self):
        parts = list(snapshot())
        parts[3] = [replace(parts[3][0], content=TEXT + "!")]
        with self.assertRaisesRegex(ValueError, "capture_integrity_mismatch"):
            invoke(parts)

    def test_duplicate_binding_rejected(self):
        parts = list(snapshot())
        parts[2] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate_source_item_binding"):
            invoke(parts)

    def test_missing_binding_rejected(self):
        parts = list(snapshot())
        parts[2] = []
        with self.assertRaisesRegex(ValueError, "incomplete_source_item_binding"):
            invoke(parts)

    def test_extra_item_rejected(self):
        parts = list(snapshot())
        parts[3].append(replace(parts[3][0], item_id=22))
        with self.assertRaisesRegex(ValueError, "incomplete_linked_items"):
            invoke(parts)

    def test_unexpected_relation_rejected(self):
        parts = list(snapshot())
        parts[4].append(EventDocumentLink(93, 11))
        with self.assertRaisesRegex(ValueError, "unexpected_event_document_link"):
            invoke(parts)

    def test_duplicate_relation_rejected(self):
        parts = list(snapshot())
        parts[4] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate_event_document_link"):
            invoke(parts)

    def test_bool_item_id_rejected(self):
        parts = list(snapshot())
        parts[2] = [SourceItemBinding("S1", True)]
        with self.assertRaisesRegex(ValueError, "invalid_source_item_binding"):
            invoke(parts)

    def test_invalid_quote_offset_rejected_by_capture_adapter(self):
        parts = list(snapshot())
        parts[5] = [QuoteOffset("S1", 0, len(TEXT) + 1)]
        with self.assertRaisesRegex(ValueError, "invalid_quote_range"):
            invoke(parts)


if __name__ == "__main__":
    unittest.main()
