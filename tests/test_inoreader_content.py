import hashlib
import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "collectors/inoreader/content_contract.py"
SPEC = importlib.util.spec_from_file_location("inoreader_content_contract", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class InoreaderContentContractTest(unittest.TestCase):
    def test_preserves_body_and_hash_but_does_not_approve_evidence(self):
        html = "<p>Factory output increased to <strong>24 systems</strong>.</p>"
        result = MODULE.extract_inoreader_content({"summary": {"content": html}})
        self.assertEqual(result["inoreader_summary_html"], html)
        self.assertEqual(result["content_sha256"], hashlib.sha256(html.encode()).hexdigest())
        self.assertEqual(result["visible_text"], "Factory output increased to 24 systems .")
        self.assertEqual(result["content_status"], "BODY_PRESENT_COMPLETENESS_UNVERIFIED")
        self.assertFalse(result["evidence_eligible"])
        self.assertEqual(result["qa_status"], "HOLD")

    def test_missing_body_is_explicit_metadata_only(self):
        result = MODULE.extract_inoreader_content({"title": "Headline only"})
        self.assertEqual(result["content_status"], "METADATA_ONLY")
        self.assertIsNone(result["content_sha256"])
        self.assertEqual(result["visible_characters"], 0)
        self.assertFalse(result["evidence_eligible"])


if __name__ == "__main__":
    unittest.main()
