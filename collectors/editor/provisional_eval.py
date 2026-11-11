"""C3-10: offline agreement with a PRIVATE, provisional P2 entailment candidate.

This is NOT the editor's production quality-gate score, independently attested
ground truth, or a source of publishing authority. No model/network/database IO.
Input text stays in memory; the returned report contains aggregate counts only.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from hashlib import sha256
import json
import re

from .holdout_contract import audit_gold_candidate

LABELS = ("supported", "contradicted", "insufficient")
_PREDICTION_SCHEMA = "bohun-c3-10-provisional-predictions-v1"
_REPORT_SCHEMA = "bohun-c3-10-provisional-agreement-v1"
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def holdout_digest(holdout: Mapping) -> str:
    """Stable hash of the full PRIVATE fixture; no text is returned or logged."""
    try:
        raw = json.dumps(holdout, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError, OverflowError):
        raise ValueError("invalid_holdout_serialization") from None
    return sha256(raw).hexdigest()


def evaluate_provisional_holdout(holdout: Mapping, predictions: Mapping) -> dict:
    """Compare exact case IDs and span citations; fail closed on missing answers.

    The candidate is source-entailment only, often including synthetic mutated
    claims. This function intentionally does not compute EditorPolicy release
    recall / false-BLOCK metrics because those require actual EditorVerdicts
    and an independently vetted production-representative gold cohort.
    """
    audit = audit_gold_candidate(holdout)
    if not isinstance(predictions, Mapping):
        raise ValueError("invalid_prediction_schema")
    if (set(predictions) != {"schema", "holdout_sha256", "cases", "publication_authorized"}
            or predictions.get("schema") != _PREDICTION_SCHEMA
            or predictions.get("publication_authorized") is not False):
        raise ValueError("invalid_prediction_schema")
    digest = predictions.get("holdout_sha256")
    if (not isinstance(digest, str) or not _HEX64.fullmatch(digest)
            or digest != holdout_digest(holdout)):
        raise ValueError("stale_or_mismatched_holdout")
    rows = predictions.get("cases")
    if not isinstance(rows, list) or len(rows) != audit["cases_count"]:
        raise ValueError("prediction_coverage_incomplete")

    ground = {c["case_id"]: c for c in holdout["cases"]}
    # Model receives all bounded quotes for one material. Citation validation
    # must enforce material lineage, not demand an annotator's one chosen span.
    # Alternate citations may be valid; label agreement is scored separately.
    material_spans: dict[str, set[str]] = {}
    for span in holdout["spans"]:
        material_spans.setdefault(span["material_id"], set()).add(span["span_id"])
    observed: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "case_id", "status", "cited_span_ids"
        }:
            raise ValueError("invalid_prediction_case")
        cid, status, refs = row["case_id"], row["status"], row["cited_span_ids"]
        if (not isinstance(cid, str) or cid not in ground or cid in observed
                or type(status) is not str or status not in LABELS
                or not isinstance(refs, list)
                or any(not isinstance(ref, str) or not ref for ref in refs)
                or len(refs) != len(set(refs))):
            raise ValueError("invalid_prediction_case")
        if (status != "insufficient" and not refs
                or not set(refs).issubset(material_spans[ground[cid]["material_id"]])):
            raise ValueError("prediction_citation_not_in_case")
        observed[cid] = status
    if set(observed) != set(ground):
        raise ValueError("prediction_coverage_incomplete")

    matrix = {expected: Counter() for expected in LABELS}
    for cid, case in ground.items():
        matrix[case["expected_source_entailment_label"]][observed[cid]] += 1
    diagonal = sum(matrix[label][label] for label in LABELS)
    count = len(ground)
    return {
        "schema": _REPORT_SCHEMA,
        "holdout_sha256": digest,
        "cases_count": count,
        "materials_count": audit["material_count"],
        "synthetic_mutation_count": audit["synthetic_mutation_count"],
        "confusion_matrix": {expected: {
            status: matrix[expected][status] for status in LABELS
        } for expected in LABELS},
        "provisional_agreement_count": diagonal,
        "provisional_agreement_rate": diagonal / count,
        "contradicted_mislabeled_supported": matrix["contradicted"]["supported"],
        "insufficient_mislabeled_supported": matrix["insufficient"]["supported"],
        "source_entailment_only": True,
        "gold_standard_attested": False,
        "real_world_truth_attested": False,
        "semantic_accuracy_attested": False,
        "editor_policy_thresholds_evaluated": False,
        "independent_p1_attested": False,
        "publication_authorized": False,
        "offline_only": True,
    }
