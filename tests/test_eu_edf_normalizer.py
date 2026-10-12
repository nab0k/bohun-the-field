"""Offline tests for EDF normalization and QA gates."""
import unittest
from collectors.opportunities.eu_edf_normalizer import normalize, normalize_page

class TestEDFNormalizer(unittest.TestCase):
    def test_complete_record_stays_on_hold(self):
        record = normalize({"metadata": {
            "identifier": ["EDF-2026-TEST"], "title": ["Example EDF call"],
            "deadlineDate": ["2026-12-31T17:00:00Z"],
            "frameworkProgramme": ["44181033"],
            "url": ["https://ec.europa.eu/info/funding-tenders/example"],
            "sortStatus": ["31094502"]}})
        self.assertEqual(record["identifier"], "EDF-2026-TEST")
        self.assertEqual(record["deadline_date"], "2026-12-31")
        self.assertEqual(record["qa_issues"], [])
        self.assertEqual(record["qa_status"], "HOLD")
        self.assertFalse(record["publication_approved"])

    def test_expired_deadline_is_flagged(self):
        record = normalize({"metadata": {
            "identifier": ["EDF-2023-DA-UWW-ASW"],
            "title": ["Example expired call"],
            "deadlineDate": ["2023-11-22T17:00:00.000+0000"],
            "frameworkProgramme": ["44181033"],
            "url": ["https://ec.europa.eu/info/funding-tenders/opportunities/data/topicDetails/example.json"],
            "sortStatus": ["2"]}})
        self.assertIn("expired_deadline", record["qa_issues"])
        self.assertEqual(record["qa_status"], "HOLD")
        self.assertFalse(record["publication_approved"])

    def test_missing_and_unsafe_fields(self):
        record = normalize({"metadata": {"url": ["https://attacker.example/grant"],
                                          "deadlineDate": ["not-a-date"]}})
        self.assertIn("missing_identifier", record["qa_issues"])
        self.assertIn("invalid_deadline", record["qa_issues"])
        self.assertIn("missing_or_nonofficial_url", record["qa_issues"])

    def test_page_counts_dedup(self):
        response = {"totalResults": 43, "results": [
            {"metadata": {"identifier": ["X"]}},
            {"metadata": {"identifier": ["X"]}},
            {"metadata": {"identifier": ["Y"]}}]}
        result = normalize_page(response)
        self.assertEqual(result["page_records"], 3)
        self.assertEqual(result["unique_identifiers_on_page"], 2)
        self.assertEqual(result["persisted"], 0)

    def test_nonlist_metadata_and_empty_results(self):
        self.assertIn("missing_identifier", normalize({"metadata": None})["qa_issues"])
        self.assertEqual(normalize_page({"results": []})["records"], [])
        with self.assertRaises(ValueError):
            normalize_page({"results": {}})

if __name__ == "__main__":
    unittest.main()
