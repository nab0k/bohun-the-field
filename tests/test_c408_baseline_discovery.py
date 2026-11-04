"""Offline C4-08 regression tests. No DB, HTTP, secrets or workflow execution."""
import importlib.util
from pathlib import Path
import sys
import unittest

PATH = Path(__file__).resolve().parents[1] / "collectors/signals/baseline_discovery.py"
SPEC = importlib.util.spec_from_file_location("c408_baseline", PATH)
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def item(**changes):
    defaults = dict(candidate_id="s1", entity="ACME", metric="missile_output",
                    unit="units/year", kind="rate", as_of="2026-10-01")
    defaults.update(changes)
    return module.Candidate(**defaults)


def source(url, text, root=None, first_seen="2025-08-02", scope="annual production, same product"):
    return module.PrimaryDocument(url, text, first_seen, root or url, scope)


def row(url, line, status="published"):
    return {"what": line, "occurred_on": "2025-08-02",
            "source_url": url, "status": status}


class BaselineTests(unittest.TestCase):
    def test_unconnected_returns_search_log_not_signal(self):
        result = module.discover(item())
        self.assertEqual(result.outcome, "not_found")
        self.assertEqual([i["status"] for i in result.searched], ["not_connected"] * 3)

    def test_read_only_local_lead_not_verified_without_document(self):
        def reader(sql, params):
            self.assertTrue(sql.startswith("SELECT "))
            self.assertIn("FROM knowledge.edge", sql)
            return [row("https://primary.example/a", "2025-08-02 ACME 12 units/year")]
        result = module.discover(item(), sql_read=reader)
        self.assertEqual(result.outcome, "needs_verification")
        self.assertIsNone(result.baseline)
        self.assertEqual(result.alternatives[0].explanation, "primary_text_not_supplied")

    def test_primary_text_verification_extracts_number_date_and_quote(self):
        url = "https://primary.example/a"
        line = "On 2025-08-02 ACME produced 12 units/year in the annual report."
        result = module.discover(item(), sql_read=lambda *_: [row(url, line)],
                                 documents={url: source(url, line)})
        self.assertEqual(result.outcome, "found")
        self.assertEqual(result.baseline.value, "12")
        self.assertEqual(result.baseline.value_date, "2025-08-02")
        self.assertEqual(result.baseline.quote, line)
        self.assertIsNone(result.second_source)

    def test_second_independent_primary_source_is_found(self):
        a, b = "https://primary.example/a", "https://primary.example/b"
        line_a = "On 2025-08-02 ACME produced 12 units/year in the annual report."
        line_b = "On 2025-09-10 ACME produced 15 units/year in the official statement."
        docs = {a: source(a, line_a), b: source(b, line_b, first_seen="2025-09-10")}
        result = module.discover(item(),
            sql_read=lambda *_: [row(a, line_a), row(b, line_b)],
            documents=docs)
        self.assertEqual(result.outcome, "found")
        self.assertIsNotNone(result.second_source)
        self.assertNotEqual(result.baseline.root_source, result.second_source.root_source)

    def test_reprinted_sources_do_not_count_twice(self):
        a, b = "https://primary.example/a", "https://media.example/b"
        la = "On 2025-08-02 ACME produced 12 units/year from the program."
        lb = "On 2025-08-03 ACME produced 12 units/year from the program."
        docs = {a: source(a, la, root="https://primary.example/root"),
                b: source(b, lb, root="https://primary.example/root")}
        result = module.discover(item(),
            sql_read=lambda *_: [row(a, la), row(b, lb)], documents=docs)
        self.assertIsNotNone(result.baseline)
        self.assertIsNone(result.second_source)

    def test_budget_caps_checks_and_marks_unverified(self):
        a, b = "https://primary.example/a", "https://primary.example/b"
        la = "On 2025-08-02 ACME produced 12 units/year from its factory."
        lb = "On 2025-08-02 ACME produced 13 units/year from its factory."
        result = module.discover(item(),
            sql_read=lambda *_: [row(a, la), row(b, lb)],
            documents={a: source(a, la), b: source(b, lb)},
            max_verifications=1)
        self.assertEqual(result.verification_count, 1)
        self.assertIsNone(result.second_source)
        self.assertTrue(any(x["status"] == "unverified_budget:1" for x in result.searched))
        self.assertTrue(any(x.explanation == "verification_budget_exhausted" for x in result.alternatives))

    def test_invalid_budget_rejected(self):
        with self.assertRaises(ValueError):
            module.discover(item(), max_verifications=module.MAX_VERIFICATIONS + 1)

    def test_mismatching_primary_quote_is_rejected(self):
        url = "https://primary.example/a"
        doc = "On 2025-08-02 ACME produced 120 units/year at this factory."
        # Proposed quote absent in primary document
        evidence = module.Evidence("local", "12", "2025-08-02",
            "On 2025-08-02 ACME produced 12 units/year at this factory.",
            url, "missile_output", "units/year", "rate", "", False, "")
        checked = module.verify_primary(item(), evidence, source(url, doc))
        self.assertFalse(checked.comparable)

    def test_wrong_unit_or_date_in_source_is_rejected(self):
        url = "https://primary.example/a"
        for text in ("On 2025-08-02 ACME produced 12 units total.",
                     "On 2025-08-02 ACME produced 12 units/year."):
            candidate = item(stage="delivered")
            lead = module.Evidence("local", None, None, None, url, candidate.metric,
                                   candidate.unit, candidate.kind, "", False, "")
            result = module.verify_primary(candidate, lead, source(url, text))
            self.assertFalse(result.comparable)

    def test_no_future_first_seen(self):
        url = "https://primary.example/a"
        line = "On 2025-08-02 ACME produced 12 units/year at its factory."
        result = module.discover(item(), sql_read=lambda *_: [row(url, line)],
            documents={url: source(url, line, first_seen="2026-10-02")})
        self.assertIsNone(result.baseline)

    def test_alias_recipient_positive_and_similar_name_negative(self):
        candidate = item(entity="Raytheon", aliases=("RAYTHEON COMPANY",),
            metric="federal_obligations", unit="USD", kind="annual_total")
        seen = []
        def post(url, body):
            seen.extend(body["filters"]["recipient_search_text"])
            return {"results": [
                {"name": "RAYTHEON COMPANY", "amount": 500, "recipient_id": "A"},
                {"name": "Raytheon Technologies", "amount": 1000, "recipient_id": "B"}]}
        result = module.discover(candidate, post_json=post)
        self.assertEqual(seen, ["Raytheon", "RAYTHEON COMPANY"])
        self.assertEqual(len(result.alternatives), 1)
        self.assertEqual(result.alternatives[0].value, "500")

    def test_legal_suffix_normalizes_but_parent_subsidiary_does_not(self):
        candidate = item(entity="Raytheon")
        self.assertTrue(module._identity(candidate, "Raytheon LLC"))
        self.assertFalse(module._identity(candidate, "Raytheon Technologies"))
        self.assertFalse(module._identity(candidate, "Raytheon Missile Systems"))

    def test_aliases_used_in_local_and_web(self):
        candidate = item(entity="Raytheon", aliases=("RAYTHEON COMPANY",))
        def reader(sql, params):
            self.assertEqual(len(params["names"]), 2)
            self.assertEqual(params["names"][1], "%RAYTHEON COMPANY%")
            self.assertIn("%missile%", params["metric_words"])
            return []
        module.local_search(candidate, reader)
        q = module.web_query(candidate)
        self.assertTrue(any("RAYTHEON COMPANY" in query for query in q))
        self.assertFalse(any("before:" in query for query in q))

    def test_sql_like_special_chars_single_escape(self):
        candidate = item(entity="AC%ME_", metric="factory_output")
        def reader(sql, params):
            self.assertEqual(params["names"][0], r"%AC\%ME\_%")
            self.assertEqual(params["metric_words"], ["%factory%", "%output%"])
            return []
        self.assertEqual(module.local_search(candidate, reader), [])

    def test_spending_not_missile_output(self):
        self.assertIsNone(module.usaspending_request(item(), "recipient"))
        self.assertEqual(module.discover(item(),
            post_json=lambda *_: self.fail("must not call")).searched[1]["status"], "not_comparable_metric")

    def test_spending_recipient_prior_year(self):
        candidate = item(metric="federal_obligations", unit="USD", kind="annual_total")
        spec = module.usaspending_request(candidate, "recipient")
        self.assertEqual(spec["body"]["filters"]["time_period"][0]["end_date"], "2025-12-31")
        self.assertIsNone(module.usaspending_request(candidate, "awarding_subagency"))

    def test_stage_rate_and_cutoff(self):
        c = item(stage="delivered")
        ev = module.Evidence("local", "12", "2025-08-02",
            "On 2025-08-02 ACME delivered 12 units/year", "https://primary.example/a",
            c.metric, c.unit, "cumulative", "delivered", False, "")
        self.assertEqual(module._compatible(c, ev)[1], "rate_vs_cumulative_or_other_kind")
        ev.kind = "rate"
        ev.stage = "announced"
        self.assertEqual(module._compatible(c, ev)[1], "different_or_unknown_stage")

    def test_status_not_used_as_event_stage(self):
        url = "https://primary.example/a"
        leads = module.local_search(item(stage="delivered"),
              lambda *_: [row(url, "ACME", status="verified")])
        self.assertEqual(leads[0].stage, "")

    def test_web_snippet_not_primary_evidence(self):
        result = module.discover(item(), search=lambda q: [{
            "url": "https://primary.example/a", "title": "ACME report",
            "snippet": "12 units/year"}])
        self.assertEqual(result.outcome, "needs_verification")
        self.assertIsNone(result.baseline)

    def test_db_error_logged_fallback_continues(self):
        def failure(*_):
            raise ConnectionError("offline")
        result = module.discover(item(), sql_read=failure, search=lambda _: [])
        self.assertEqual(result.searched[0]["status"], "error:ConnectionError")


if __name__ == "__main__":
    unittest.main()
