"""C4-11: offline invariants for cutoff-only research packets (no network)."""
from datetime import date
import json
from pathlib import Path
import unittest

PACKETS = Path(__file__).with_name("C4-11_FIVE_CUTOFF_PACKETS.json")
LEADS = Path(__file__).with_name("C4-11_FIVE_CASE_PILOT.json")
LATER_KEYS = {"later_outcome", "known_future_outcome", "expected_event_label",
              "model_prediction", "signal_label", "outcome_observed_at",
              "outcome_status", "later_evidence_url", "later_evidence_date"}


class CutoffOnlyPacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(PACKETS.read_text(encoding="utf-8"))
        cls.pilot = json.loads(LEADS.read_text(encoding="utf-8"))

    def test_five_unique_matching_ids(self):
        records = self.payload["records"]
        self.assertEqual(len(records), 5)
        ids = [item["case_id"] for item in records]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {case["lead_id"] for case in self.pilot["cases"]})

    def test_cutoff_source_dates_strictly_before_cutoff(self):
        for item in self.payload["records"]:
            with self.subTest(case=item["case_id"]):
                self.assertLess(date.fromisoformat(item["source"]["publication_date"]),
                                date.fromisoformat(item["cutoff_date"]))

    def test_cutoff_source_matches_original_research(self):
        source_by_id = {item["lead_id"]: item for item in self.pilot["cases"]}
        for item in self.payload["records"]:
            original = source_by_id[item["case_id"]]
            self.assertEqual(item["source"]["url"], original["cutoff_source"]["url"])
            self.assertEqual(item["source"]["publication_date"],
                             original["cutoff_source"]["publication_date"])

    def test_no_outcome_fields_anywhere(self):
        def walk(value):
            if isinstance(value, dict):
                self.assertFalse(LATER_KEYS.intersection(value.keys()),
                                 "A later outcome was copied into an input packet")
                for nested in value.values():
                    walk(nested)
            elif isinstance(value, list):
                for nested in value:
                    walk(nested)
        walk(self.payload)
        self.assertTrue(self.payload["ground_truth_not_included"])

    def test_no_evidence_claimed_historically_verified(self):
        self.assertFalse(self.payload["historical_replay_eligible"])
        self.assertFalse(self.payload["complete_c4_10_gate_available"])
        for item in self.payload["records"]:
            src = item["source"]
            self.assertIsNone(src["first_seen_at"])
            self.assertIsNone(src["archived_snapshot_sha256"])
            self.assertFalse(src["original_version_verified"])
            self.assertEqual(item["assessment"]["signal_qualification"], "HOLD")
            self.assertFalse(item["assessment"]["publication_allowed"])

    def test_each_has_concrete_question_stage_and_bounded_inferences(self):
        for item in self.payload["records"]:
            with self.subTest(case=item["case_id"]):
                self.assertTrue(item["stage_as_of_cutoff"])
                self.assertTrue(item["analyst_question"])
                self.assertTrue(item["forbidden_inferences"])
                self.assertTrue(item["cutoff_facts"])
                self.assertEqual(item["assessment"]["baseline"], "not_established")

    def test_money_keeps_unit_and_stage(self):
        by_id = {item["case_id"]: item for item in self.payload["records"]}
        edirpa = by_id["C4-11-R11"]["numeric_claims"]
        epf = by_id["C4-11-R12"]["numeric_claims"]
        self.assertEqual((edirpa[0]["amount"], edirpa[0]["currency"],
                          edirpa[0]["stage"]),
                         ("500000000", "EUR", "proposed"))
        self.assertEqual((epf[0]["amount"], epf[0]["currency"],
                          epf[0]["stage"]),
                         ("2000000000", "EUR_2018_prices", "political_agreement"))


if __name__ == "__main__":
    unittest.main()
