"""EDF paginated audit offline tests."""
import unittest
from collectors.opportunities.eu_edf_paginated_audit import audit

def hit(identifier, deadline):
    return {"metadata": {"identifier": [identifier], "title": ["Example"],
                         "deadlineDate": [deadline], "frameworkProgramme": ["44181033"],
                         "url": ["https://ec.europa.eu/info/funding-tenders/example"]}}

class TestAudit(unittest.TestCase):
    def test_paginated_duplicates_and_expiry(self):
        pages = {1: {"totalResults": 3, "results": [hit("A", "2023-11-22"), hit("B", "2023-11-22")]},
                 2: {"totalResults": 3, "results": [hit("A", "2023-11-22")]}}
        result = audit(3, 2, lambda page, size: pages[page])
        self.assertTrue(result["complete"])
        self.assertEqual(result["processed"], 3)
        self.assertEqual(result["unique_identifiers"], 2)
        self.assertEqual(result["duplicate_identifiers"], 1)
        self.assertEqual(result["qa_issue_counts"]["expired_deadline"], 3)
        self.assertEqual(result["persisted"], 0)

    def test_incomplete_page_limit(self):
        result = audit(1, 1, lambda page, size: {"totalResults": 2,
                                                 "results": [hit("A", "2023-11-22")]})
        self.assertFalse(result["complete"])

    def test_changed_total_fails_closed(self):
        def fetch(page, size):
            return {"totalResults": 3 if page == 1 else 4,
                    "results": [hit(str(page), "2023-11-22")]}
        with self.assertRaises(ValueError):
            audit(2, 1, fetch)

if __name__ == "__main__":
    unittest.main()
