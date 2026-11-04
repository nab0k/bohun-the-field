"""Offline tests for C4-08; no database, network, workflow or secrets."""
import importlib.util
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "collectors/signals/baseline_discovery.py"
SPEC = importlib.util.spec_from_file_location("c408_baseline", PATH)
import sys
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)

def item(**changes):
    data = dict(candidate_id="s1", entity="ACME", metric="missile_output",
                unit="units/year", kind="rate", as_of="2026-10-01")
    data.update(changes)
    return module.Candidate(**data)

class BaselineTests(unittest.TestCase):
    def test_unconnected_fails_open_for_research_but_not_to_signal(self):
        result = module.discover(item())
        self.assertEqual(result.outcome, "not_found")
        self.assertEqual([x["status"] for x in result.searched], ["not_connected"] * 3)

    def test_local_queries_read_only_and_no_assumed_value(self):
        def reader(sql, params):
            self.assertTrue(sql.startswith("SELECT "))
            self.assertIn("FROM knowledge.edge", sql)
            self.assertIn("cutoff", params)
            return [{"what": "ACME ESSM pace reported", "occurred_on": "2025-08-02",
                     "source_url": "https://example.org/a", "status": "announced"}]
        result = module.discover(item(), sql_read=reader)
        self.assertEqual(result.outcome, "needs_verification")
        self.assertIsNone(result.alternatives[0].value)
        self.assertFalse(result.alternatives[0].comparable)

    def test_local_verified_baseline_stops_before_external_queries(self):
        def verify(candidate, evidence):
            evidence.value = "12"
            evidence.metric = candidate.metric
            evidence.kind = candidate.kind
            evidence.unit = candidate.unit
            evidence.stage = candidate.stage
            evidence.verification = "verified"
            return evidence
        result = module.discover(
            item(), sql_read=lambda *_: [{
                "what": "12 units/year", "occurred_on": "2025-08-02",
                "source_url": "https://example.org/a", "status": ""}],
            post_json=lambda *_: self.fail("USAspending should not be used"),
            search=lambda _: self.fail("Web search should not be used"),
            verify=verify)
        self.assertEqual(result.outcome, "found")
        self.assertEqual(result.baseline.value, "12")

    def test_usaspending_rejects_capacity_as_money(self):
        self.assertIsNone(module.usaspending_request(item(), "recipient"))
        result = module.discover(item(), post_json=lambda *_: self.fail("no USASpending call"))
        self.assertEqual(result.searched[1]["status"], "not_comparable_metric")

    def test_usaspending_request_uses_recipient_and_prior_year(self):
        candidate = item(metric="federal_obligations", unit="USD", kind="annual_total")
        request = module.usaspending_request(candidate, "recipient")
        self.assertEqual(request["body"]["filters"]["recipient_search_text"], ["ACME"])
        self.assertEqual(request["body"]["filters"]["time_period"][0]["end_date"], "2025-12-31")
        self.assertIsNone(module.usaspending_request(candidate, "awarding_subagency"))

    def test_usaspending_aggregate_is_not_verified(self):
        candidate = item(metric="federal_obligations", unit="USD", kind="annual_total")
        result = module.discover(candidate, post_json=lambda *_: {"results": [
            {"name": "ACME", "amount": 1234.5, "recipient_id": "r1"},
            {"name": "ACME OTHER", "amount": 999999}]})
        self.assertEqual(result.outcome, "needs_verification")
        self.assertEqual(len(result.alternatives), 1)
        self.assertEqual(result.alternatives[0].value, "1234.5")

    def test_stages_and_rate_must_match(self):
        candidate = item(stage="delivered")
        evidence = module.Evidence("local", "20", "2025-01-01", "20 shipped",
                                   "https://example.org/a", "missile_output",
                                   "units/year", "rate", "announced", False, "")
        self.assertEqual(module._compatible(candidate, evidence)[1], "different_stage")
        evidence.stage = "delivered"
        evidence.kind = "cumulative"
        self.assertEqual(module._compatible(candidate, evidence)[1], "rate_vs_cumulative_or_other_kind")

    def test_date_cutoff_prevents_future_leak(self):
        e = module.Evidence("local", "20", "2026-10-02", "20", "https://example.org/a",
                            "missile_output", "units/year", "rate", "", False, "")
        self.assertEqual(module._compatible(item(), e)[1], "after_cutoff")

    def test_duplicate_root_source_counts_once(self):
        e = lambda url: module.Evidence("web", None, None, "snippet", url,
                     "missile_output", "units/year", "rate", "", False, "",
                     "https://official.example/statement")
        self.assertEqual(len(module._dedupe([e("https://media.example/a"),
                                             e("https://media.example/b")])), 1)

    def test_web_snippets_cannot_be_verified_baselines(self):
        result = module.discover(item(), search=lambda q: [{
            "url": "https://official.example/notice", "title": "Announcement",
            "snippet": "production increased"}])
        self.assertEqual(result.outcome, "needs_verification")
        self.assertEqual(len(result.alternatives), 1)
        self.assertIsNone(result.baseline)

    def test_missing_date_and_value_not_comparable(self):
        e = module.Evidence("local", None, None, "quote", "https://example.org",
                            "missile_output", "units/year", "rate", "", False, "")
        self.assertEqual(module._compatible(item(), e)[1], "unknown_value_date")

    def test_error_logged_and_fallback_continues(self):
        def fail(*_):
            raise ConnectionError("offline")
        result = module.discover(item(), sql_read=fail, search=lambda _: [])
        self.assertEqual(result.searched[0]["status"], "error:ConnectionError")
        self.assertEqual(result.outcome, "not_found")

if __name__ == "__main__":
    unittest.main()
