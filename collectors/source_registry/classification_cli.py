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
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from .classification import ClassificationError, assess_sample, classify_batch, MAX_SOURCES, MAX_ID

MAX_INPUT_BYTES = 1_000_000
MAX_PROPOSAL_BYTES = 128_000


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


def _proposals(payload: Any, sources: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Precomputed, untrusted model outputs, with exact source-ID binding."""
    if not isinstance(payload, dict) or set(payload) != {"proposals"}:
        raise ClassificationError("PROPOSAL_ENVELOPE")
    rows = payload["proposals"]
    if not isinstance(rows, list) or len(rows) > MAX_SOURCES:
        raise ClassificationError("PROPOSAL_LIMIT")
    known = {s["source_id"] for s in sources}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "source_id", "source_class", "evidence_quote"}:
            raise ClassificationError("PROPOSAL_FIELDS")
        sid = row["source_id"]
        if not isinstance(sid, str) or not (0 < len(sid) <= MAX_ID) or sid not in known:
            raise ClassificationError("PROPOSAL_UNKNOWN_SOURCE")
        if sid in out:
            raise ClassificationError("PROPOSAL_DUPLICATE_SOURCE")
        out[sid] = row
    return out


def build_report(source_payload: Any, gold_payload: Any = None,
                 *, require_138: bool = False,
                 sample_seed: str = "C1-02-owner-review-v1",
                 proposal_payload: Any = None) -> dict[str, Any]:
    if not isinstance(source_payload, dict) or set(source_payload) != {"sources"}:
        raise ClassificationError("SOURCE_ENVELOPE")
    if not isinstance(sample_seed, str) or not 1 <= len(sample_seed) <= 128:
        raise ClassificationError("SAMPLE_SEED_INVALID")
    sources = source_payload["sources"]
    proposals = _proposals(proposal_payload, sources) if proposal_payload is not None else {}
    result = classify_batch(sources, propose=lambda row: proposals.get(row["source_id"])) if proposals else classify_batch(sources)
    # Stable SHA-256 ranking across all source IDs makes review selection
    # reproducible and independent of input order or predicted class.
    ranked_ids = sorted((r.source_id for r in result),
                        key=lambda sid: (hashlib.sha256((sample_seed + ":" + sid).encode("utf-8")).hexdigest(), sid))
    review_sample = ranked_ids[:50] if len(ranked_ids) >= 50 else []
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
        "review_sample_seed": sample_seed,
        "review_sample_source_ids": review_sample,
        "human_gold_assessment": None,
        "external_proposals_supplied": len(proposals),
        "external_proposals_accepted": sum(x.class_origin == "external_proposal" for x in result),
        "owner_approved": False,
        "network_or_live_probes_run": False,
    }
    if gold_payload is not None:
        if not isinstance(gold_payload, dict) or set(gold_payload) != {"labels"}:
            raise ClassificationError("GOLD_ENVELOPE")
        gold_labels = gold_payload["labels"]
        if not isinstance(gold_labels, dict) or set(gold_labels) != set(review_sample):
            raise ClassificationError("GOLD_SAMPLE_IDS_MISMATCH")
        report["human_gold_assessment"] = assess_sample(result, gold_labels)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="C1-02 read-only source classification")
    parser.add_argument("--input", required=True, help="local JSON source snapshot")
    parser.add_argument("--gold", help="optional local JSON with 50 independent human labels")
    parser.add_argument("--proposals", help="optional local JSON of precomputed, untrusted model proposals")
    parser.add_argument("--require-138", action="store_true",
                        help="fail if not 138 sources with confirmed delivery evidence")
    parser.add_argument("--seed", default="C1-02-owner-review-v1",
                        help="fixed public seed used to select 50 review IDs")
    args = parser.parse_args(argv)
    try:
        report = build_report(_load(args.input), _load(args.gold) if args.gold else None,
                              require_138=args.require_138, sample_seed=args.seed,
                              proposal_payload=_load(args.proposals) if args.proposals else None)
    except (ClassificationError, ValueError, TypeError) as exc:
        code = str(exc)
        if code not in {
            "DUPLICATE_JSON_KEY", "NONFINITE_JSON", "INPUT_FILE_INVALID", "INVALID_JSON",
            "SOURCE_ENVELOPE", "GOLD_ENVELOPE", "BATCH_SIZE", "DUPLICATE_SOURCE_ID",
            "INVALID_SOURCE", "INVALID_SOURCE_ID", "INVALID_TITLE", "EMPTY_TITLE",
            "INVALID_DESCRIPTION", "INVALID_SAMPLES", "INVALID_URL", "INVALID_DELIVERY",
            "GOLD_SAMPLE_REQUIREMENT", "GOLD_MISMATCH",
            "138_VERIFIED_DELIVERY_NOT_ESTABLISHED", "SAMPLE_SEED_INVALID",
            "GOLD_SAMPLE_IDS_MISMATCH", "PROPOSAL_ENVELOPE", "PROPOSAL_LIMIT",
            "PROPOSAL_FIELDS", "PROPOSAL_UNKNOWN_SOURCE", "PROPOSAL_DUPLICATE_SOURCE",
        }:
            code = "CLASSIFICATION_FAILED"
        print(json.dumps({"error_code": code}), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
