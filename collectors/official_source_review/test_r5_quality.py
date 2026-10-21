"""R5 regression checks, fully offline: no external APIs or data writes."""
import json
import unittest

from r5_quality import count_quality, live_quality


class R5QualityTests(unittest.TestCase):
    def test_uk_kind_and_hold(self):
        rows = [{"ocid": "ocds-1", "id": "a", "tag": ["tender"],
                 "tender": {"title": "Private title"}},
                {"ocid": "ocds-1", "id": "b", "tag": ["award"],
                 "tender": {"title": "Private title"}}]
        result = count_quality("uk", rows, complete=True)
        self.assertEqual(result["candidate_kinds"],
                         {"contract_award_notice": 1, "procurement_notice": 1})
        self.assertTrue(result["complete"])
        self.assertFalse(result["ready_for_import"])
        self.assertNotIn("Private title", json.dumps(result))

    def test_duplicate_and_conflicting_versions(self):
        a = {"referenceNumber-numeroReference": "SENSITIVE-42", "title-titre-eng": "alpha"}
        b = dict(a, **{"title-titre-eng": "changed"})
        result = count_quality("ca_new", [a, a, b], complete=True)
        self.assertEqual((result["records_seen"], result["unique_candidates"],
                          result["duplicate_observations"], result["conflicting_versions"]),
                         (3, 1, 1, 1))
        self.assertNotIn("SENSITIVE-42", json.dumps(result))

    def test_malformed_rejected_without_leak(self):
        result = count_quality("ca_open", [
            {"title-titre-eng": "confidential"},
            {"referenceNumber-numeroReference": "r", "title-titre-eng": "valid"}],
            complete=True)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["unique_candidates"], 1)
        self.assertNotIn("confidential", json.dumps(result))

    def test_eu_unverified_partial(self):
        result = count_quality("eu", [
            {"reference": "x", "metadata": {"title": ["EDF"], "type": ["1"]}},
            {"reference": "y", "metadata": {"title": ["Other"], "type": ["999"]}}],
            complete=False, reported_total=415)
        self.assertEqual(result["unique_candidates"], 2)
        self.assertEqual(result["candidate_kinds"], {"eu_unclassified": 1, "grant_call_candidate": 1})
        self.assertTrue(result["sample_only"])
        self.assertEqual(result["defence_relevance_verified"], 0)

    def test_live_uk_two_pages(self):
        calls = []
        def transport(url, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                body = {"releases": [{"ocid": "x", "id": "1", "tag": ["tender"], "tender": {"title": "t"}}],
                        "links": {"next": "?cursor=abc"}}
            else:
                body = {"releases": [{"ocid": "x", "id": "2", "tag": ["award"], "tender": {"title": "t"}}]}
            return json.dumps(body).encode()
        report = live_quality("uk", transport=transport)
        self.assertEqual(report["records_seen"], 2)
        self.assertEqual(report["pages_checked"], 2)
        self.assertEqual(report["source_response_count"], 2)

    def test_live_canada_with_bom(self):
        raw = "﻿referenceNumber-numeroReference,title-titre-eng\nZ,Secret\n".encode()
        report = live_quality("ca_new", transport=lambda url, **kwargs: raw)
        self.assertEqual(report["unique_candidates"], 1)
        self.assertNotIn("Secret", json.dumps(report))

    def test_live_eu_partial_is_report_not_failure(self):
        raw = json.dumps({"results": [{"reference": "1", "metadata": {"title": ["x"], "type": ["1"]}}],
                          "totalResults": 415}).encode()
        report = live_quality("eu", transport=lambda url, **kwargs: raw)
        self.assertEqual(report["reported_total"], 415)
        self.assertFalse(report["complete"])
        self.assertEqual(report["unique_candidates"], 1)

    def test_bad_source_rejected(self):
        with self.assertRaises(ValueError):
            count_quality("other", [], complete=True)
        with self.assertRaises(ValueError):
            live_quality("other", transport=lambda x: b"{}")

    def test_empty_valid_snapshot_not_published(self):
        result = count_quality("uk", [], complete=True)
        self.assertEqual(result["unique_candidates"], 0)
        self.assertEqual(result["qa"], "HOLD")
        self.assertFalse(result["public_release"])


if __name__ == "__main__":
    unittest.main()
