#!/usr/bin/env python3
"""Free re-scoring of the paid series records (no Gemini calls).

Input: docs/evidence-phase1-results/run-38324082330.json (claims + char offsets as
produced by the models) and the same source texts: fixtures from the repo, PAC-3
from bohun_staging (READ ONLY), bounded to the first 12,000 chars as before.
Each stored span is verified against the text (fixtures: saved preview; PAC-3:
SHA-256 of the 160-char preview) before it is used.

Re-applies the post-series guards and MiniCheck with two windows (sentence and
paragraph) and reports old vs new. IN-SAMPLE: the fixes were designed after seeing
these very records, so improvements here are not evidence of generalisation.
Prints results as GitHub ::notice annotations.
"""
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from intelligence.evidence_phase1 import documents as D  # noqa: E402
from intelligence.evidence_phase1.entailment import MiniCheckScorer, apply_entailment  # noqa: E402
from intelligence.evidence_phase1.guards import apply_guards  # noqa: E402
from intelligence.evidence_phase1.metrics import aggregate, score_against_gold  # noqa: E402
from intelligence.evidence_phase1.record import build_record, validate_record  # noqa: E402

RESULTS = ROOT / "docs/evidence-phase1-results/run-38324082330.json"
OUT = ROOT / "output/evidence-phase1"
PILOT_CHARS = 12000


def notice(title, payload):
    msg = json.dumps(payload, ensure_ascii=False).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::notice title={title}::{msg}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    saved = json.loads(RESULTS.read_text())
    docs = {d["document_id"]: d for d in D.load_fixtures(["syn-contract-negation", "syn-cancelled-dates"])}
    errors = []
    try:
        pac = D.load_staging(limit=1, title_like="PAC-3")[0]
        pac = dict(pac, text=pac["text"][:PILOT_CHARS])
        docs[pac["document_id"]] = pac
    except Exception as exc:
        errors.append("STAGING_READ_FAILED:" + type(exc).__name__)

    def find_doc(suffix):
        hits = [d for k, d in docs.items() if k.endswith(suffix)]
        return hits[0] if len(hits) == 1 else None

    scorer = MiniCheckScorer(cache_dir=str(ROOT / "ckpts"))
    report = {"source_run": saved["run_id"], "in_sample_warning": True, "errors": errors, "experiments": {}}
    for label, exp in saved["experiments"].items():
        if label.startswith("e0") or "records" not in exp:
            continue
        old, new, skipped, integrity = [], [], 0, 0
        for r in exp["records"]:
            doc = find_doc(r["doc"])
            if doc is None or r["chars"][0] is None:
                skipped += 1
                continue
            s, e = r["chars"]
            ev = doc["text"][s:e]
            if "evidence_preview_sha256" in r:
                ok = hashlib.sha256(ev[:160].encode()).hexdigest() == r["evidence_preview_sha256"]
            else:
                ok = ev[:160] == r.get("evidence")
            if not ok:
                integrity += 1
                continue
            rec = build_record(document=doc, extractor_name=r.get("ext") or label, candidate={
                "claim_text": r["claim"], "evidence_text": ev, "char_start": s, "char_end": e,
                "modality": r["modality"], "asserted_by": r["asserted_by"], "event_date": r["event_date"]})
            rec["checks"] = {"claim_text_possibly_truncated": len(r["claim"]) >= 160}
            apply_guards(rec, doc["text"])
            old.append(r)
            new.append(rec)
        apply_entailment(new, {k: d["text"] for k, d in docs.items()}, scorer, wide_context=True)
        problems = [p for rec in new for p in validate_record(rec, docs[rec["document_id"]]["text"])]

        def count(reasons_list, key):
            return sum(any(h.startswith(key) for h in hs) for hs in reasons_list)

        old_h = [r["hold"] for r in old]
        new_h = [r["hold_reasons"] for r in new]
        keys = ["PLAN_CUE_IN_SPAN", "CANCELLATION_CUE", "SPECULATIVE_CUE", "NEGATION_CUE_IN_SPAN",
                "CLAIM_DROPS_PLAN", "CLAIM_DROPS_SPECULATIVE", "CLAIM_DROPS_NEGATION",
                "ENTAILMENT_NOT_SUPPORTED", "ENTAILMENT_NEEDS_WIDER_CONTEXT"]
        gold = None
        if label.startswith(("e1", "e2")):
            by_doc = collections.defaultdict(list)
            for rec in new:
                by_doc[rec["document_id"]].append(rec)
            gold = aggregate(new, [score_against_gold(v, docs[k]) for k, v in by_doc.items()])
        report["experiments"][label] = {
            "rescored": len(new), "skipped_unaligned_or_unknown_doc": skipped, "integrity_failures": integrity,
            "validation_problems": len(problems),
            "all_hold": all(x["verification_status"] == "HOLD" and x["public_release"] is False for x in new),
            "flag_counts_old_vs_new": {k: [count(old_h, k), count(new_h, k)] for k in keys},
            "entailment_old_vs_new_sentence": [[o["ent"][0], n["entailment_label"]] for o, n in zip(old, new)],
            "metrics_fixtures": gold,
            "records": [{"claim": n["claim_text"][:120], "modality": n["modality"],
                         "ent_sentence": [n["entailment_label"], n["entailment_score"]],
                         "ent_wide": [n.get("entailment_label_wide"), n.get("entailment_score_wide")],
                         "hold_new": n["hold_reasons"]} for n in new],
        }
    (OUT / "rescore.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    notice("rescore-overview", {k: v for k, v in report.items() if k != "experiments"}
           | {lab: {k: v for k, v in x.items() if k not in ("records", "entailment_old_vs_new_sentence")}
              for lab, x in report["experiments"].items()})
    for lab, x in report["experiments"].items():
        for i in range(0, len(x["records"]), 8):
            notice(f"rescore-{lab}-{i // 8 + 1}", x["records"][i:i + 8])
    bad = [x for x in report["experiments"].values() if x["validation_problems"] or not x["all_hold"]]
    return 2 if bad or errors else 0


if __name__ == "__main__":
    sys.exit(main())
