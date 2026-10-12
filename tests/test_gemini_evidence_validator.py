import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "intelligence/gemini_evidence_validator.py"
SPEC = importlib.util.spec_from_file_location("gemini_evidence_validator", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


PACKET = {
    "evidence": [
        {"evidence_id": "ev-1", "exact_excerpt": "<amount>24000000</amount>"},
        {"evidence_id": "ev-2", "exact_excerpt": "<date>2026-01-15</date>"},
    ]
}


def candidate(span="24000000", evidence_id="ev-1", qa_status="HOLD"):
    return {
        "qa_status": qa_status,
        "candidate_claims": [{
            "claim_id": "candidate-claim-1",
            "subject": "programme-1",
            "predicate": "amount",
            "object": {"value": "24000000", "datatype": "decimal", "unit": "EUR"},
            "evidence_ids": [evidence_id],
            "exact_spans": [span],
            "confidence": 0.9,
        }],
        "candidate_event": {"type": "funding", "claim_ids": ["candidate-claim-1"]},
    }


class GeminiEvidenceValidatorTest(unittest.TestCase):
    def test_accepts_supported_candidate_but_keeps_hold(self):
        result = MODULE.validate_candidate(PACKET, candidate())
        self.assertTrue(result["pass"])
        self.assertEqual(result["qa_status"], "HOLD")
        self.assertEqual(result["exact_spans_verified"], 1)

    def test_rejects_plausible_but_unsupported_claim(self):
        with self.assertRaisesRegex(MODULE.CandidateValidationError, "UNSUPPORTED_EXACT_SPAN"):
            MODULE.validate_candidate(PACKET, candidate(span="25000000"))

    def test_rejects_unknown_evidence_id(self):
        with self.assertRaisesRegex(MODULE.CandidateValidationError, "UNKNOWN_EVIDENCE_ID"):
            MODULE.validate_candidate(PACKET, candidate(evidence_id="ev-missing"))

    def test_rejects_model_attempt_to_approve(self):
        with self.assertRaisesRegex(MODULE.CandidateValidationError, "MUST_REMAIN_HOLD"):
            MODULE.validate_candidate(PACKET, candidate(qa_status="APPROVE"))


if __name__ == "__main__":
    unittest.main()
