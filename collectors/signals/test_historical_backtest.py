"""C4-11 offline tests: no HTTP, database or live signal inference."""
from copy import deepcopy
from datetime import date, timedelta
import unittest

from collectors.signals.historical_backtest import evaluate


def panel(n=20):
    cases, predictions = [], []
    for index in range(n):
        id_ = f"case-{index:02d}"
        truth = "signal" if index % 2 == 0 else "no_signal"
        decision = truth
        cases.append({
            "case_id": id_, "cutoff_date": "2025-06-01",
            "outcome": truth, "outcome_observed_at": "2025-07-01",
            "outcome_source": "human_review:fixture",
        })
        predictions.append({
            "case_id": id_, "cutoff_date": "2025-06-01",
            "decision": decision, "rule_version": "frozen-v1",
            "used_outcome": False,
            "evidence": [{"source_url": "https://example.org/archived/item",
                          "available_at": "2025-05-31",
                          "event_at": "2025-05-30"}],
        })
    return (
        {"schema": "bohun-c4-11-labels-v1", "locked": True,
         "label_version": "fixture-v1", "cases": cases},
        {"schema": "bohun-c4-11-predictions-v1",
         "label_version": "fixture-v1", "predictions": predictions},
    )


class HistoricalBacktestTests(unittest.TestCase):
    def test_perfect_panel_counts_positive_and_negative(self):
        labels, preds = panel()
        out = evaluate(labels, preds)
        self.assertEqual((out["tp"], out["tn"], out["fp"], out["fn"]), (10, 10, 0, 0))
        self.assertEqual(out["precision"], 1)
        self.assertEqual(out["false_positive_rate"], 0)
        self.assertEqual(out["status"], "evaluated_not_validated")

    def test_false_positive_and_false_negative(self):
        labels, preds = panel()
        preds["predictions"][1]["decision"] = "signal"
        preds["predictions"][0]["decision"] = "no_signal"
        out = evaluate(labels, preds)
        self.assertEqual((out["tp"], out["tn"], out["fp"], out["fn"]), (9, 9, 1, 1))
        self.assertEqual(out["accuracy"], 0.9)
        self.assertEqual(out["precision"], 0.9)
        self.assertEqual(out["false_positive_rate"], 0.1)

    def test_less_than_twenty_cases_refused(self):
        labels, preds = panel(19)
        with self.assertRaisesRegex(ValueError, "at least 20"):
            evaluate(labels, preds)

    def test_not_enough_negatives_refused(self):
        labels, preds = panel()
        for case in labels["cases"]:
            case["outcome"] = "signal"
        with self.assertRaisesRegex(ValueError, "negative"):
            evaluate(labels, preds)

    def test_unlocked_labels_refused(self):
        labels, preds = panel()
        labels["locked"] = False
        with self.assertRaisesRegex(ValueError, "locked"):
            evaluate(labels, preds)

    def test_mismatched_prediction_or_label_version_refused(self):
        labels, preds = panel()
        preds["label_version"] = "different"
        with self.assertRaisesRegex(ValueError, "versions"):
            evaluate(labels, preds)

    def test_missing_extra_or_duplicate_ids_refused(self):
        for mutation in ("missing", "extra", "duplicate"):
            with self.subTest(mutation=mutation):
                labels, preds = panel()
                if mutation == "missing":
                    preds["predictions"].pop()
                elif mutation == "extra":
                    preds["predictions"].append({**preds["predictions"][-1],
                                                 "case_id": "case-999"})
                else:
                    preds["predictions"][1]["case_id"] = preds["predictions"][0]["case_id"]
                with self.assertRaises(ValueError):
                    evaluate(labels, preds)

    def test_future_source_availability_refused(self):
        labels, preds = panel()
        preds["predictions"][0]["evidence"][0]["available_at"] = "2025-06-02"
        with self.assertRaisesRegex(ValueError, "post-cutoff source"):
            evaluate(labels, preds)

    def test_future_event_date_refused(self):
        labels, preds = panel()
        preds["predictions"][0]["evidence"][0]["event_at"] = "2025-06-03"
        with self.assertRaisesRegex(ValueError, "post-cutoff event"):
            evaluate(labels, preds)

    def test_outcome_leakage_flag_refused(self):
        labels, preds = panel()
        preds["predictions"][0]["used_outcome"] = True
        with self.assertRaisesRegex(ValueError, "outcome leakage"):
            evaluate(labels, preds)

    def test_outcome_date_must_follow_cutoff(self):
        labels, preds = panel()
        labels["cases"][0]["outcome_observed_at"] = "2025-05-31"
        with self.assertRaisesRegex(ValueError, "predates cutoff"):
            evaluate(labels, preds)

    def test_missing_provenance_refused(self):
        labels, preds = panel()
        preds["predictions"][0]["evidence"] = []
        with self.assertRaisesRegex(ValueError, "missing evidence"):
            evaluate(labels, preds)

    def test_not_just_any_url(self):
        labels, preds = panel()
        preds["predictions"][0]["evidence"][0]["source_url"] = "http://example.org"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            evaluate(labels, preds)

    def test_missing_rule_version_refused(self):
        labels, preds = panel()
        preds["predictions"][0]["rule_version"] = ""
        with self.assertRaisesRegex(ValueError, "rule_version"):
            evaluate(labels, preds)


if __name__ == "__main__":
    unittest.main()
