"""C4-11: offline, fail-closed retrospective evaluation of signal decisions.

Inputs are explicitly pre-registered human labels and model predictions. This
module neither creates historical cases nor makes a prediction. No network/DB.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
from collections import Counter
from datetime import date
import json
from pathlib import Path

MIN_CASES = 20
MIN_NEGATIVES = 5
DECISIONS = frozenset({"signal", "no_signal"})


def _date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be ISO date") from exc


def _records(value: object, label: str) -> list[dict]:
    if not isinstance(value, list) or any(not isinstance(x, dict) for x in value):
        raise ValueError(f"{label} must be an array of objects")
    return value


def _ids(rows: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        identifier = row.get("case_id")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in result:
            raise ValueError(f"{label} has blank or duplicate case_id")
        result[identifier] = row
    return result


def evaluate(labels: object, predictions: object, *, min_cases: int = MIN_CASES) -> dict:
    """Reject incomplete panels and any disclosed post-cutoff model evidence.

    Human outcomes may be measured later; they must not be placed into a
    model's pre-cutoff evidence list. This checks submitted provenance only;
    verifying source timestamps and actual model isolation needs an audit.
    """
    if not isinstance(labels, dict) or not isinstance(predictions, dict):
        raise ValueError("inputs must be JSON objects")
    if labels.get("schema") != "bohun-c4-11-labels-v1":
        raise ValueError("invalid labels schema")
    if predictions.get("schema") != "bohun-c4-11-predictions-v1":
        raise ValueError("invalid predictions schema")
    if labels.get("locked") is not True:
        raise ValueError("human labels must be pre-registered and locked")
    if not isinstance(labels.get("label_version"), str) or not labels["label_version"].strip():
        raise ValueError("label_version is required")
    if predictions.get("label_version") != labels["label_version"]:
        raise ValueError("prediction and label versions mismatch")
    cases = _ids(_records(labels.get("cases"), "cases"), "labels")
    preds = _ids(_records(predictions.get("predictions"), "predictions"), "predictions")
    if len(cases) < min_cases:
        raise ValueError(f"at least {min_cases} cases required")
    if set(cases) != set(preds):
        raise ValueError("predictions must match exactly the locked case IDs")

    counts = Counter()
    by_case = []
    negative_count = 0
    for identifier in sorted(cases):
        case, pred = cases[identifier], preds[identifier]
        cutoff = _date(case.get("cutoff_date"), "cutoff_date")
        if pred.get("cutoff_date") != case["cutoff_date"]:
            raise ValueError(f"{identifier}: prediction cutoff mismatch")
        truth = case.get("outcome")
        decision = pred.get("decision")
        if truth not in DECISIONS or decision not in DECISIONS:
            raise ValueError(f"{identifier}: invalid decision or ground truth")
        if not isinstance(case.get("outcome_source"), str) or not case["outcome_source"].strip():
            raise ValueError(f"{identifier}: missing independently reviewed outcome_source")
        observed = _date(case.get("outcome_observed_at"), "outcome_observed_at")
        if observed < cutoff:
            raise ValueError(f"{identifier}: historical outcome predates cutoff; audit time design")
        if truth == "no_signal":
            negative_count += 1
        if not isinstance(pred.get("rule_version"), str) or not pred["rule_version"].strip():
            raise ValueError(f"{identifier}: missing rule_version")
        if pred.get("used_outcome") is not False:
            raise ValueError(f"{identifier}: outcome leakage flag not explicitly false")
        evidence = _records(pred.get("evidence"), "evidence")
        if not evidence:
            raise ValueError(f"{identifier}: missing evidence/provenance")
        for item in evidence:
            url = item.get("source_url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError(f"{identifier}: missing HTTPS source_url")
            available = _date(item.get("available_at"), "available_at")
            if available > cutoff:
                raise ValueError(f"{identifier}: post-cutoff source leakage")
            if item.get("event_at") is not None:
                event = _date(item["event_at"], "event_at")
                if event > cutoff:
                    raise ValueError(f"{identifier}: post-cutoff event leakage")
        bucket = ("tp" if truth == "signal" and decision == "signal" else
                  "fn" if truth == "signal" else
                  "fp" if decision == "signal" else "tn")
        counts[bucket] += 1
        by_case.append({"case_id": identifier, "cutoff_date": cutoff.isoformat(),
                        "truth": truth, "decision": decision, "result": bucket})
    if negative_count < MIN_NEGATIVES and min_cases >= MIN_CASES:
        raise ValueError(f"at least {MIN_NEGATIVES} negative cases required")
    tp, tn, fp, fn = (counts[k] for k in ("tp", "tn", "fp", "fn"))
    safe_div = lambda n, d: round(n / d, 4) if d else None
    return {
        "status": "evaluated_not_validated", "cases": len(cases),
        "negative_cases": negative_count, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
        "accuracy": safe_div(tp + tn, len(cases)),
        "precision": safe_div(tp, tp + fp), "recall": safe_div(tp, tp + fn),
        "false_positive_rate": safe_div(fp, fp + tn),
        "false_discovery_rate": safe_div(fp, tp + fp),
        "by_case": by_case,
        "warning": ("Only submitted provenance/time boundaries checked. "
                    "True frozen historical inputs, independent labels and "
                    "rule pre-registration require external audit."),
    }


def _read_with_digest(path: str) -> tuple[object, str]:
    """Hash the exact input bytes, not a re-serialized JSON representation."""
    p = Path(path)
    with p.open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("input exceeds 2 MiB")
    digest = hashlib.sha256(raw).hexdigest()
    return json.loads(raw.decode("utf-8")), digest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", required=True, help="locked human outcomes JSON")
    parser.add_argument("--predictions", required=True, help="model decision provenance JSON")
    parser.add_argument("--expected-labels-sha256", required=True,
                        help="SHA-256 of the pre-registered raw labels JSON file; "
                             "must come from a separate trusted record")
    args = parser.parse_args(argv)
    try:
        expected = args.expected_labels_sha256.lower()
        if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
            raise ValueError("expected-labels-sha256 must be a 64-character hex digest")
        labels, label_digest = _read_with_digest(args.labels)
        if not hmac.compare_digest(label_digest, expected):
            raise ValueError("labels SHA-256 does not match the pre-registered digest")
        predictions, prediction_digest = _read_with_digest(args.predictions)
        report = evaluate(labels, predictions)
        report["labels_sha256"] = label_digest
        report["predictions_sha256"] = prediction_digest
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "reason": str(exc)[:200]}))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
