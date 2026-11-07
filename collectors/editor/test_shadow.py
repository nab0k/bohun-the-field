"""C3-10 offline shadow adapter regression cases. Synthetic, no external calls."""
import unittest
from collectors.editor.shadow import shadow_review


def row(**changes):
    base = {
        "id": 42, "event_id": 91, "version": 1,
        "headline": "A contract announcement",
        "what_happened": "An award was announced.",
        "why_it_matters": "It may affect a supply chain.",
        "generator": "fixture-writer", "published_at": "2026-11-06T12:00:00Z",
        "sources": [{"url": "https://example.org/announcement",
                     "date": "2026-11-05", "class": "P1"}],
        "context": "A cited historical event.",
        "context_sources": [{"url": "https://example.org/background",
                             "line": "A previous announcement occurred."}],
    }
    return {**base, **changes}


class ShadowTests(unittest.TestCase):
    def test_no_model_or_verified_evidence_cannot_pass(self):
        result = shadow_review([row()])
        self.assertEqual(result["decisions"]["REVIEW"], 1)
        self.assertFalse(result["publication_authorized"])
        self.assertIn("independent_semantic_assessment_missing",
                      result["results"][0]["reason_codes"])

    def test_exported_fake_verification_is_ignored(self):
        result = shadow_review([row(retrieval_verified=True, coverage_complete=True)])
        self.assertEqual(result["decisions"]["PASS"], 0)

    def test_no_article_body_in_report(self):
        report = str(shadow_review([row()]))
        self.assertNotIn("An award was announced.", report)
        self.assertNotIn("A cited historical event.", report)

    def test_snapshot_identity_rejects_bool(self):
        with self.assertRaisesRegex(ValueError, "invalid_snapshot_identity"):
            shadow_review([row(id=True)])

    def test_snapshot_duplicate_id_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate_snapshot_material"):
            shadow_review([row(), row()])

    def test_snapshot_limit_enforced(self):
        with self.assertRaises(ValueError):
            shadow_review([row(id=i + 1) for i in range(31)])

    def test_insecure_source_blocks_deterministically(self):
        result = shadow_review([row(sources=[{"url": "http://localhost/a",
                                              "date": "2026-11-05"}])])
        self.assertEqual(result["decisions"]["BLOCK"], 1)

    def test_missing_mandatory_section_blocks(self):
        result = shadow_review([row(what_happened="")])
        self.assertEqual(result["decisions"]["BLOCK"], 1)

    def test_untrusted_context_remains_unverified(self):
        result = shadow_review([row()])
        self.assertIn("context_evidence_linkage_unverified",
                      result["results"][0]["integration_gaps"])

    def test_sources_are_never_marked_retrieved(self):
        result = shadow_review([row()])
        self.assertIn("source_not_independently_retrieved",
                      result["results"][0]["reason_codes"])


if __name__ == "__main__":
    unittest.main()
