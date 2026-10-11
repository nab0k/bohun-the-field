"""Read-only EDF response structure inspection tests."""
import unittest
from collectors.opportunities.eu_funding_api_smoke import summarize_response

class TestEDFResults(unittest.TestCase):
    def test_empty_results(self):
        result = summarize_response({"totalResults": 0, "results": [], "warnings": []})
        self.assertEqual(result["total_results"], 0)
        self.assertEqual(result["returned_count"], 0)
        self.assertEqual(result["sample_hits"], [])

    def test_bounded_preview(self):
        hits = [{"title": "Example", "identifier": "EDF-123", "description": "private detail"}] * 8
        result = summarize_response({"totalResults": 8, "results": hits})
        self.assertEqual(result["returned_count"], 8)
        self.assertEqual(len(result["sample_hits"]), 3)
        self.assertNotIn("description", result["sample_hits"][0]["scalar_candidates"])

    def test_rejects_unexpected_schema(self):
        with self.assertRaises(ValueError):
            summarize_response({"results": {"unexpected": "object"}})

if __name__ == "__main__":
    unittest.main()
