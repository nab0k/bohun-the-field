"""Offline tests for safe EDF nested schema discovery."""
import unittest
from collectors.opportunities.eu_funding_api_smoke import nested_shape, summarize_response

class TestNestedShape(unittest.TestCase):
    def test_metadata_dict(self):
        result = nested_shape({"metadata": {"deadlineDate": "2030-01-01", "title": "Secret"},
                               "enrichedMetadata": {"budget": 500},
                               "reference": "ID123"})
        self.assertEqual(result["metadata"]["type"], "dict")
        self.assertIn("deadlineDate", result["metadata"]["keys"])
        self.assertNotIn("2030-01-01", str(result))
        self.assertNotIn("Secret", str(result))
        self.assertNotIn("ID123", str(result))

    def test_metadata_list(self):
        result = nested_shape({"metadata": [{"key": "deadline", "value": "hidden"}]})
        self.assertEqual(result["metadata"]["length"], 1)
        self.assertEqual(result["metadata"]["first_keys"], ["key", "value"])
        self.assertNotIn("hidden", str(result))

    def test_summary_contains_nested_shape(self):
        result = summarize_response({"totalResults": 1, "results": [
            {"metadata": {"deadline": "2027-01-01"}}]})
        self.assertIn("nested_shape", result["sample_hits"][0])

if __name__ == "__main__":
    unittest.main()
