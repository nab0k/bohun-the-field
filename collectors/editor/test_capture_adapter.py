"""Offline, synthetic C3-10 capture binding checks: no LLM or database calls."""
import unittest
from dataclasses import replace
from hashlib import sha256

from collectors.editor.capture_adapter import (LocalCapture, QuoteOffset,
                                               attach_local_captures)
from collectors.editor.core import (EvidenceBundle, EvidenceSource,
                                     PublishCandidate, review)


URL = "https://example.org/award"
SOURCE = EvidenceSource("S1", URL, "example.org", "2026-11-06", (), False)
TEXT = "Award announced: 1 000 units. The delivery is not yet confirmed."


def sample():
    cap = LocalCapture("S1", URL, TEXT, sha256(TEXT.encode("utf-8")).hexdigest())
    return EvidenceBundle((SOURCE,), ("event-91",)), cap


class CaptureAdapterTests(unittest.TestCase):
    def test_quote_is_exact_and_deterministic(self):
        evidence, cap = sample()
        loc = QuoteOffset("S1", 0, 28)
        a = attach_local_captures(evidence, [cap], [loc])
        b = attach_local_captures(evidence, [cap], [loc])
        self.assertEqual(a, b)
        self.assertEqual(a.sources[0].spans[0].text, TEXT[0:28])
        self.assertTrue(a.sources[0].spans[0].span_id.startswith("capture-"))
        self.assertEqual(a.event_ids, evidence.event_ids)

    def test_forged_verified_flag_gets_cleared(self):
        evidence, cap = sample()
        forged = replace(evidence, sources=(replace(SOURCE, retrieval_verified=True),))
        result = attach_local_captures(forged, [cap], [QuoteOffset("S1", 0, 15)])
        self.assertIs(result.sources[0].retrieval_verified, False)

    def test_changed_capture_text_fails_hash(self):
        evidence, cap = sample()
        with self.assertRaisesRegex(ValueError, "capture_integrity_mismatch"):
            attach_local_captures(evidence, [replace(cap, content=cap.content + "!")], [])

    def test_wrong_capture_url_is_rejected(self):
        evidence, cap = sample()
        with self.assertRaisesRegex(ValueError, "capture_url_mismatch"):
            attach_local_captures(evidence, [replace(cap, url="https://other.org")], [])

    def test_missing_capture_is_rejected(self):
        evidence, _ = sample()
        with self.assertRaisesRegex(ValueError, "missing_capture_source"):
            attach_local_captures(evidence, [], [])

    def test_extra_capture_source_is_rejected(self):
        evidence, cap = sample()
        with self.assertRaisesRegex(ValueError, "unexpected_capture_source"):
            attach_local_captures(evidence, [cap, replace(cap, source_id="S2")], [])

    def test_duplicate_capture_source_is_rejected(self):
        evidence, cap = sample()
        with self.assertRaisesRegex(ValueError, "duplicate_capture_source"):
            attach_local_captures(evidence, [cap, cap], [])

    def test_bool_offset_is_rejected(self):
        evidence, cap = sample()
        with self.assertRaisesRegex(ValueError, "invalid_quote_range"):
            attach_local_captures(evidence, [cap], [QuoteOffset("S1", True, 10)])

    def test_out_of_bounds_quote_is_rejected(self):
        evidence, cap = sample()
        with self.assertRaisesRegex(ValueError, "invalid_quote_range"):
            attach_local_captures(evidence, [cap], [QuoteOffset("S1", 0, len(TEXT) + 1)])

    def test_duplicate_quote_offset_is_rejected(self):
        evidence, cap = sample()
        loc = QuoteOffset("S1", 0, 15)
        with self.assertRaisesRegex(ValueError, "duplicate_quote_offset"):
            attach_local_captures(evidence, [cap], [loc, loc])

    def test_only_whitespace_quote_is_rejected(self):
        evidence, cap = sample()
        start = TEXT.index(" ")
        with self.assertRaisesRegex(ValueError, "empty_quote_text"):
            attach_local_captures(evidence, [cap], [QuoteOffset("S1", start, start + 1)])

    def test_unknown_quote_source_is_rejected(self):
        evidence, cap = sample()
        with self.assertRaisesRegex(ValueError, "unknown_quote_source"):
            attach_local_captures(evidence, [cap], [QuoteOffset("S2", 0, 10)])

    def test_normal_review_cannot_pass_just_with_capture(self):
        evidence, cap = sample()
        ev = attach_local_captures(evidence, [cap], [QuoteOffset("S1", 0, 25)])
        candidate = PublishCandidate(
            "syn-42", "synthesis", 1,
            {"headline": "Contract announcement", "what_happened": "Award announced.",
             "why_it_matters": "It may affect capacity.", "as_of": "2026-11-07",
             "sources": [{"source_id": "S1", "url": URL}]},
            "writer-a", ("event-91",), "")
        verdict = review(candidate, ev, assessment=None)
        self.assertEqual(verdict.decision, "REVIEW")
        self.assertTrue(verdict.offline_only)
        self.assertIn("source_not_independently_retrieved", [r.code for r in verdict.reasons])


if __name__ == "__main__":
    unittest.main()
