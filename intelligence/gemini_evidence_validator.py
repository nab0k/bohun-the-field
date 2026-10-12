"""Deterministic checks for model-produced intelligence candidates.

Structured model output is never treated as evidence. This validator only accepts
candidate claims whose evidence IDs resolve and whose exact spans occur in the
stored evidence excerpts supplied to the model.
"""


class CandidateValidationError(ValueError):
    pass


def validate_candidate(packet, candidate):
    if candidate.get("qa_status") != "HOLD":
        raise CandidateValidationError("MODEL_OUTPUT_MUST_REMAIN_HOLD")
    evidence = {item["evidence_id"]: item for item in packet.get("evidence", [])}
    if not evidence:
        raise CandidateValidationError("NO_STORED_EVIDENCE")
    claims = candidate.get("candidate_claims")
    if not isinstance(claims, list):
        raise CandidateValidationError("CANDIDATE_CLAIMS_MUST_BE_LIST")
    seen_claim_ids = set()
    for claim in claims:
        claim_id = claim.get("claim_id")
        if not claim_id or claim_id in seen_claim_ids:
            raise CandidateValidationError("MISSING_OR_DUPLICATE_CLAIM_ID")
        seen_claim_ids.add(claim_id)
        evidence_ids = claim.get("evidence_ids")
        spans = claim.get("exact_spans")
        if not evidence_ids or not spans:
            raise CandidateValidationError(f"CLAIM_WITHOUT_EVIDENCE:{claim_id}")
        if any(evidence_id not in evidence for evidence_id in evidence_ids):
            raise CandidateValidationError(f"UNKNOWN_EVIDENCE_ID:{claim_id}")
        excerpts = [evidence[evidence_id]["exact_excerpt"] for evidence_id in evidence_ids]
        for span in spans:
            if not isinstance(span, str) or not span.strip():
                raise CandidateValidationError(f"EMPTY_EXACT_SPAN:{claim_id}")
            if not any(span in excerpt for excerpt in excerpts):
                raise CandidateValidationError(f"UNSUPPORTED_EXACT_SPAN:{claim_id}")
    event = candidate.get("candidate_event") or {}
    event_claim_ids = event.get("claim_ids") or []
    if any(claim_id not in seen_claim_ids for claim_id in event_claim_ids):
        raise CandidateValidationError("EVENT_REFERENCES_UNKNOWN_CLAIM")
    return {
        "pass": True,
        "qa_status": "HOLD",
        "claims_verified": len(claims),
        "exact_spans_verified": sum(len(claim["exact_spans"]) for claim in claims),
    }
