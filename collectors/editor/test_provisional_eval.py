"""Offline synthetic tests for provisional source-entailment *agreement*, not truth."""
from copy import deepcopy
from hashlib import sha256
import json
import unittest

from collectors.editor.provisional_eval import (
    evaluate_provisional_holdout, holdout_digest,
)
from collectors.editor.test_holdout_contract import good


def fixture():
    data = good()
    data["cases"][0]["case_id"] = "c-supported"
    data["cases"][0]["origin"] = "source_faithful"
    negative = deepcopy(data["cases"][0])
    negative["case_id"] = "c-contradicted"
    negative["claim_text"] = "The publisher did not announce forty units."
    negative["claim_sha256"] = sha256(negative["claim_text"].encode()).hexdigest()
    negative["expected_source_entailment_label"] = "contradicted"
    negative["origin"] = "synthetic_mutation"
    unresolved = deepcopy(data["cases"][0])
    unresolved["case_id"] = "c-insufficient"
    unresolved["claim_text"] = "The shipment reached the buyer after testing."
    unresolved["claim_sha256"] = sha256(unresolved["claim_text"].encode()).hexdigest()
    unresolved["expected_source_entailment_label"] = "insufficient"
    data["cases"].extend((negative, unresolved))
    return data


def responses(data):
    return {
        "schema": "bohun-c3-10-provisional-predictions-v1",
        "holdout_sha256": holdout_digest(data),
        "publication_authorized": False,
        "cases": [
            {"case_id": "c-supported", "status": "supported",
             "cited_span_ids": ["span-1"]},
            {"case_id": "c-contradicted", "status": "insufficient",
             "cited_span_ids": []},
            {"case_id": "c-insufficient", "status": "supported",
             "cited_span_ids": ["span-1"]},
        ],
    }


class ProvisionalEvaluatorTests(unittest.TestCase):
    def test_matrix_uses_gold_rows_and_reports_high_risk_supported(self):
        data = fixture()
        report = evaluate_provisional_holdout(data, responses(data))
        self.assertEqual(report["cases_count"], 3)
        self.assertEqual(report["provisional_agreement_count"], 1)
        self.assertEqual(report["provisional_agreement_rate"], 1/3)
        self.assertEqual(report["confusion_matrix"]["contradicted"]["insufficient"], 1)
        self.assertEqual(report["confusion_matrix"]["insufficient"]["supported"], 1)
        self.assertEqual(report["insufficient_mislabeled_supported"], 1)
        self.assertEqual(report["synthetic_mutation_count"], 1)
        self.assertFalse(report["gold_standard_attested"])
        self.assertFalse(report["editor_policy_thresholds_evaluated"])
        self.assertFalse(report["publication_authorized"])

    def test_perfect_provisional_agreement_never_authorizes(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][1]["status"] = "contradicted"
        pred["cases"][1]["cited_span_ids"] = ["span-1"]
        pred["cases"][2]["status"] = "insufficient"
        pred["cases"][2]["cited_span_ids"] = []
        report = evaluate_provisional_holdout(data, pred)
        self.assertEqual(report["provisional_agreement_rate"], 1.0)
        self.assertFalse(report["semantic_accuracy_attested"])
        self.assertFalse(report["publication_authorized"])

    def test_metadata_only_does_not_leak_quotes_or_claims(self):
        data = fixture()
        report = evaluate_provisional_holdout(data, responses(data))
        serialized = json.dumps(report)
        for case in data["cases"]:
            self.assertNotIn(case["claim_text"], serialized)
        self.assertNotIn(data["spans"][0]["text"], serialized)
        self.assertNotIn("claim_text", serialized)
        self.assertNotIn("expected_source_entailment_label", serialized)

    def test_digest_stable_under_json_object_key_order(self):
        data = fixture()
        changed = dict(reversed(list(data.items())))
        self.assertEqual(holdout_digest(data), holdout_digest(changed))

    def test_digest_pins_exact_private_fixture(self):
        data = fixture()
        pred = responses(data)
        data["cases"][0]["claim_text"] = "Changed"
        data["cases"][0]["claim_sha256"] = sha256(b"Changed").hexdigest()
        with self.assertRaisesRegex(ValueError, "stale_or_mismatched_holdout"):
            evaluate_provisional_holdout(data, pred)

    def test_tampered_holdout_is_rejected_before_scoring(self):
        data = fixture()
        pred = responses(data)
        data["spans"][0]["text"] += " changed"
        with self.assertRaisesRegex(ValueError, "span_integrity_mismatch|span_offsets_inconsistent"):
            evaluate_provisional_holdout(data, pred)

    def test_missing_prediction_fails_closed(self):
        data = fixture()
        pred = responses(data)
        pred["cases"].pop()
        with self.assertRaisesRegex(ValueError, "prediction_coverage_incomplete"):
            evaluate_provisional_holdout(data, pred)

    def test_extra_prediction_fails_closed(self):
        data = fixture()
        pred = responses(data)
        pred["cases"].append({"case_id": "invented", "status": "supported",
                              "cited_span_ids": ["span-1"]})
        with self.assertRaisesRegex(ValueError, "prediction_coverage_incomplete"):
            evaluate_provisional_holdout(data, pred)

    def test_duplicate_id_fails_closed(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][1]["case_id"] = "c-supported"
        with self.assertRaisesRegex(ValueError, "invalid_prediction_case"):
            evaluate_provisional_holdout(data, pred)

    def test_wrong_case_id_fails_closed(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][0]["case_id"] = "other"
        with self.assertRaisesRegex(ValueError, "invalid_prediction_case"):
            evaluate_provisional_holdout(data, pred)

    def test_unknown_status_fails_closed(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][0]["status"] = "published"
        with self.assertRaisesRegex(ValueError, "invalid_prediction_case"):
            evaluate_provisional_holdout(data, pred)

    def test_invalid_citation_fails_closed(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][0]["cited_span_ids"] = ["from_other_story"]
        with self.assertRaisesRegex(ValueError, "prediction_citation_not_in_case"):
            evaluate_provisional_holdout(data, pred)

    def test_alternate_span_from_same_material_is_valid_citation(self):
        data = fixture()
        alternate = deepcopy(data["spans"][0])
        alternate["span_id"] = "span-alternate"
        alternate["text"] = "The original purchase remains subject to review."
        alternate["end_char_exclusive"] = (
            alternate["start_char_0"] + len(alternate["text"]))
        alternate["text_sha256"] = sha256(alternate["text"].encode()).hexdigest()
        data["spans"].append(alternate)
        # The annotator's case still cites only span-1. The model may correctly
        # cite another quote from the same supplied material instead.
        self.assertEqual(data["cases"][0]["quoted_span_ids"], ["span-1"])
        pred = responses(data)
        pred["cases"][0]["cited_span_ids"] = ["span-alternate"]
        report = evaluate_provisional_holdout(data, pred)
        self.assertEqual(report["cases_count"], 3)
        self.assertFalse(report["publication_authorized"])

    def test_supported_without_any_citation_fails_closed(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][0]["cited_span_ids"] = []
        with self.assertRaisesRegex(ValueError, "prediction_citation_not_in_case"):
            evaluate_provisional_holdout(data, pred)

    def test_nonstring_and_unhashable_citation_fails_closed(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][0]["cited_span_ids"] = [["span-1"]]
        with self.assertRaisesRegex(ValueError, "invalid_prediction_case"):
            evaluate_provisional_holdout(data, pred)

    def test_duplicate_citations_fails_closed(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][0]["cited_span_ids"] = ["span-1", "span-1"]
        with self.assertRaisesRegex(ValueError, "invalid_prediction_case"):
            evaluate_provisional_holdout(data, pred)

    def test_no_untrusted_labels_or_source_text_in_predictions(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][0]["expected_source_entailment_label"] = "supported"
        with self.assertRaisesRegex(ValueError, "invalid_prediction_case"):
            evaluate_provisional_holdout(data, pred)

    def test_prediction_cannot_assert_publication_authority(self):
        data = fixture()
        pred = responses(data)
        pred["publication_authorized"] = True
        with self.assertRaisesRegex(ValueError, "invalid_prediction_schema"):
            evaluate_provisional_holdout(data, pred)

    def test_untrusted_checker_claim_does_not_replace_case_id(self):
        data = fixture()
        pred = responses(data)
        pred["cases"][0]["case_id"] = "c-" + "1"*24
        with self.assertRaisesRegex(ValueError, "invalid_prediction_case"):
            evaluate_provisional_holdout(data, pred)


if __name__ == "__main__":
    unittest.main()
