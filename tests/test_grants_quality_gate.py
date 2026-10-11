#!/usr/bin/env python3
"""Offline tests for Grants.gov QA screening. No external services."""
import datetime as dt
import unittest
from collectors.opportunities.audit_grants import audit

TODAY = dt.date(2026, 10, 11)

def item(external_id, status="posted", close="11/06/2026", **overrides):
    record = {"external_id": external_id, "title": "Sample grant",
              "agency": "", "agency_code": "DOD-AMC",
              "status": status, "close_date": close}
    record.update(overrides)
    return record

class GrantsAuditTests(unittest.TestCase):
    def test_current_posted_is_candidate_not_approved(self):
        result = audit({"items": [item("1")]}, TODAY)
        self.assertEqual(result["potentially_current_posted"], 1)
        self.assertEqual(result["publication_approved"], 0)
        self.assertEqual(result["missing_agency_name"], 1)

    def test_forecast_missing_date_held(self):
        result = audit({"items": [item("1", "forecasted", "")]}, TODAY)
        self.assertEqual(result["forecasted"], 1)
        self.assertEqual(result["missing_close_date"], 1)
        self.assertEqual(result["held"], 1)

    def test_expired_posted_held(self):
        result = audit({"items": [item("1", close="01/01/2024")]}, TODAY)
        self.assertEqual(result["close_date_before_today"], 1)
        self.assertEqual(result["held"], 1)

    def test_invalid_date_held(self):
        result = audit({"items": [item("1", close="2026-11-06")]}, TODAY)
        self.assertEqual(result["invalid_close_date"], 1)
        self.assertEqual(result["held"], 1)

    def test_missing_agency_code_held(self):
        result = audit({"items": [item("1", agency_code="")]}, TODAY)
        self.assertEqual(result["missing_agency_code"], 1)
        self.assertEqual(result["held"], 1)

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(ValueError):
            audit({"items": [item("1"), item("1")]}, TODAY)

    def test_empty_and_missing_ids_rejected(self):
        for items in ([], [item("")]):
            with self.subTest(items=items), self.assertRaises(ValueError):
                audit({"items": items}, TODAY)

if __name__ == "__main__":
    unittest.main()
