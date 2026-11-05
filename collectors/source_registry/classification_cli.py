"""C1-02 offline read-only report runner. No HTTP, model or subscription writes.

Usage:
  python -B -m collectors.source_registry.classification_cli --input /private/feeds.json
  python -B -m collectors.source_registry.classification_cli --input /private/feeds.json \
      --gold /private/50_labels.json --require-138

Input: {"sources": [{source_id,title,url,description,sample_titles,
                    delivery_evidence:[{method,status,ref}]}]}
Gold:  {"labels": {"source-id": "grants", ...}} (exactly 50 labels)

Output to stdout is a bounded JSON report, never source text or credentials.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

from .classification import ClassificationError, assess_sample, classify_batch

MAX_INPUT_BYTES = 1_000_000


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output = {}
    for key, value in pairs:
        if key in output:
            raise ClassificationError("DUPLICATE_JSON_KEY")
        output[key] = value
    return output


def _invalid_number(_: str) -> None:
    raise ClassificationError("NONFINITE_JSON")


def _load(path: str) -> Any:
    """One local file, bounded bytes, duplicate-key and NaN rejection."""
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > MAX_INPUT_BYTES:
            raise ClassificationError("INPUT_FILE_INVALID")
        data = p.read_bytes()
        if len(data) > MAX_INPUT_BYTES:
            raise ClassificationError("INPUT_FILE_INVALID")
    except OSError as exc:
        raise ClassificationError("INPUT_FILE_INVALID") from exc
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                          parse_constant=_invalid_number)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ClassificationError("INVALID_JSON") from exc


def build_report(source_payload: Any, gold_payload: Any = None,
                 *, require_138: bool = False) -> dict[str, Any]:
    if not isinstance(source_payload, dict) or set(source_payload) != {"sources"}:
        raise ClassificationError("SOURCE_ENVELOPE")
    sources = source_payload["sources"]
    result = classify_batch(sources)
    count = len(result)
    classes = Counter(x.source_class for x in result)
    class_review = sum(x.class_status == "needs_review" for x in result)
    delivery = Counter(x.delivery_method for x in result)
    delivery_unknown = sum(x.delivery_status != "evidenced" for x in result)
    # Explicit probe records are input claims, not proof the target works live.
    if require_138 and (count != 138 or delivery_unknown):
        raise ClassificationError("138_VERIFIED_DELIVERY_NOT_ESTABLISHED")
    report: dict[str, Any] = {
        "schema": "C1-02-offline-report-v1",
        "source_count": count,
        "class_counts": {k: classes.get(k, 0) for k in (
            "grants", "tenders", "procurement", "company_news", "media",
            "investments", "startups", "technology", "laws", "other")},
        "needs_class_review": class_review,
        "delivery_counts": {k: delivery.get(k, 0) for k in (
            "rss_atom", "api", "web_monitor", "email", "manual", "unknown")},
        "delivery_unverified_or_conflicting": delivery_unknown,
        "classifications": [x.json_dict() for x in result],
        "human_gold_assessment": None,
        "owner_approved": False,
        "network_or_live_probes_run": False,
    }
    if gold_payload is not None:
        if not isinstance(gold_payload, dict) or set(gold_payload) != {"labels"}:
            raise ClassificationError("GOLD_ENVELOPE")
        report["human_gold_assessment"] = assess_sample(result, gold_payload["labels"])
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C1-02 read-only source classification")
    parser.add_argument("--input", required=True, help="local JSON source snapshot")
    parser.add_argument("--gold", help="optional local JSON with 50 independent human labels")
    parser.add_argument("--require-138", action="store_true",
                        help="fail if not 138 sources with confirmed delivery evidence")
    args = parser.parse_args(argv)
    try:
        report = build_report(_load(args.input), _load(args.gold) if args.gold else None,
                              require_138=args.require_138)
    except (ClassificationError, ValueError, TypeError) as exc:
        code = str(exc)
        if code not in {
            "DUPLICATE_JSON_KEY", "NONFINITE_JSON", "INPUT_FILE_INVALID", "INVALID_JSON",
            "SOURCE_ENVELOPE", "GOLD_ENVELOPE", "BATCH_SIZE", "DUPLICATE_SOURCE_ID",
            "INVALID_SOURCE", "INVALID_SOURCE_ID", "INVALID_TITLE", "EMPTY_TITLE",
            "INVALID_DESCRIPTION", "INVALID_SAMPLES", "INVALID_URL", "INVALID_DELIVERY",
            "GOLD_SAMPLE_REQUIREMENT", "GOLD_MISMATCH",
            "138_VERIFIED_DELIVERY_NOT_ESTABLISHED",
        }:
            code = "CLASSIFICATION_FAILED"
        print(json.dumps({"error_code": code}), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
