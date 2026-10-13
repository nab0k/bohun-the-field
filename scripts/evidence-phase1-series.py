#!/usr/bin/env python3
"""Evidence Phase 1 paid series (owner-approved 2026-10-13, total authorization $1).

One process, one BudgetLedger shared by every paid call, so the cap covers the
whole series. Software limit, not a billing guarantee (see budget.py).

Experiments (identical inputs where methods are compared):
  E0 baseline   fixtures  replay (hand-written candidates, no LLM)        + MiniCheck
  E1            fixtures  Gemini-direct                                    + MiniCheck
  E2            fixtures  LangExtract 1.7.1 + Gemini (budgeted provider)   + MiniCheck
  E3a baseline  PAC-3     existing pilot claim from bohun_staging (read)   + MiniCheck
  E3b           PAC-3     Gemini-direct                                    + MiniCheck
  E3c           PAC-3     LangExtract + Gemini                             + MiniCheck
PAC-3 text = first 12,000 chars of the stored visible text, exactly the input the
pilot gave Gemini, for all three PAC-3 methods.
"""
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from intelligence.evidence_phase1 import documents as D  # noqa: E402
from intelligence.evidence_phase1.budget import (BudgetExceeded, BudgetLedger, budgeted_langextract_model,  # noqa: E402
                                                 budgeted_transport)
from intelligence.evidence_phase1.entailment import MiniCheckScorer  # noqa: E402
from intelligence.evidence_phase1.extractors import GeminiDirectExtractor, LangExtractExtractor  # noqa: E402
from intelligence.evidence_phase1.pipeline import ReplayExtractor, run  # noqa: E402

OUT = ROOT / "output/evidence-phase1"
MODEL = "gemini-3.5-flash-lite"
CAP_USD = 0.50            # half of the $1 authorization, as a safety margin
MAX_CALLS = 40
MAX_INPUT_TOKENS_PER_CALL = 8000
MAX_OUTPUT_TOKENS = 2048  # includes thinking tokens (Gemini docs)
PILOT_CHARS = 12000       # same bound as scripts/inoreader-gemini-staging-pilot.py
LX_BUFFER = 1500
# Spend already booked by earlier runs of this series (ledger usageMetadata, paid-tier prices):
# run 38323701774 (commit 3c06885): 4 calls, $0.005300.
PRIOR_SPEND_USD = 0.0053


def preflight(fixtures, pac3):
    """Worst-case plan before any paid call. Tokens bounded by chars/2 (conservative)."""
    def worst(input_chars, calls):
        tokens = input_chars / 2 + 800
        return calls * (tokens * 0.30 + MAX_OUTPUT_TOKENS * 2.50) / 1e6
    plan = []
    for d in fixtures + ([pac3] if pac3 else []):
        if d["text_status"] != "TEXT_PRESENT_COMPLETENESS_UNVERIFIED":
            continue
        n = len(d["text"])
        lx_calls = 2 * math.ceil(n / LX_BUFFER) + 1  # sentence-aware chunking can split more finely
        plan.append({"doc": d["document_id"], "direct_calls": 1, "langextract_calls_max": lx_calls,
                     "worst_usd": round(worst(n, 1) + worst(LX_BUFFER + 2500, lx_calls), 5)})
    total = round(sum(p["worst_usd"] for p in plan), 5)
    calls = sum(p["direct_calls"] + p["langextract_calls_max"] for p in plan)
    return {"plan": plan, "worst_case_total_usd": total, "prior_spend_usd": PRIOR_SPEND_USD,
            "planned_calls_max": calls, "fits_cap": PRIOR_SPEND_USD + total <= CAP_USD and calls <= MAX_CALLS}


def pilot_baseline_candidates(doc):
    cands = []
    for row in D.load_pilot_candidates(doc["document_id"]):
        for c in (row["response"] or {}).get("candidate_claims", []):
            obj = c.get("object") or {}
            cands.append({"claim_text": " ".join(str(x) for x in (c.get("subject"), c.get("predicate"), obj.get("value")) if x),
                          "evidence_text": (c.get("exact_spans") or [""])[0], "modality": None, "asserted_by": None,
                          "event_date": None, "_pilot": {"model_run_id": row["model_run_id"], "model": row["model"],
                                                          "usage": row["usage"], "created_at": row["created_at"]}})
    return cands


def save(label, result, extra=None):
    with open(OUT / f"{label}-records.jsonl", "w", encoding="utf-8") as fh:
        for r in result["records"]:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {k: v for k, v in result.items() if k != "records"}
    summary["run"] = {"label": label, **(extra or {})}
    (OUT / f"{label}-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    log = {"started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "experiments": {}, "errors": []}
    fixtures = D.load_fixtures(["syn-contract-negation", "syn-cancelled-dates", "syn-no-full-text"])
    pac3 = None
    try:
        staged = D.load_staging(limit=1, title_like="PAC-3")
        if staged:
            pac3 = dict(staged[0])
            full_len = len(pac3["text"])
            pac3["text"] = pac3["text"][:PILOT_CHARS]
            pac3["text_status"] = D.text_status(pac3["text"])
            pac3["text_extraction"] = f"pilot visible_text(html), first {PILOT_CHARS} of {full_len} chars (same as pilot input)"
            log["pac3"] = {"document_id": pac3["document_id"], "source_url": pac3["source_url"], "title": pac3.get("title"),
                           "publication_date": pac3["publication_date"], "stored_text_chars": full_len,
                           "experiment_text_chars": len(pac3["text"])}
        else:
            log["errors"].append("PAC3_NOT_FOUND_IN_STAGING")
    except Exception as exc:
        log["errors"].append("STAGING_READ_FAILED:" + type(exc).__name__ + ":" + str(exc)[:160])

    pf = preflight(fixtures, pac3)
    log["preflight"] = pf
    print(json.dumps({"preflight": pf}, indent=2))

    t0 = time.perf_counter()
    scorer = MiniCheckScorer(cache_dir=str(ROOT / "ckpts"))
    log["minicheck"] = {"model": scorer.label, "load_s": getattr(scorer, "load_seconds", None),
                        "commit": "b58b9fa69acbd1015ec970fa65dd752413a053d2"}

    def run_one(label, docs, extractor, **meta):
        s = time.perf_counter()
        try:
            res = run(docs, extractor, scorer, max_docs=3)
        except BudgetExceeded as exc:
            log["errors"].append(f"{label}:BUDGET:{exc}")
            return
        save(label, res, meta)
        log["experiments"][label] = {"seconds": round(time.perf_counter() - s, 2), "metrics": res["metrics"],
                                     "entailment_run": res["entailment_run"],
                                     "validation_problems": len(res["validation_problems"]),
                                     "documents": [{k: d.get(k) for k in ("document_id", "outcome", "candidates", "text_chars")}
                                                   for d in res["documents"]]}

    # E0 baseline: no LLM
    run_one("e0-fixtures-replay", fixtures,
            ReplayExtractor(json.loads((ROOT / "intelligence/fixtures/evidence_phase1/replay/candidates.json").read_text())),
            method="replay (hand-written, deliberate errors)")
    if pac3:
        try:
            base = pilot_baseline_candidates(pac3)
            log["pilot_baseline"] = [c["_pilot"] for c in base]
            run_one("e3a-pac3-pilot-baseline", [pac3], ReplayExtractor({pac3["document_id"]: base}),
                    method="existing pilot claim (bohun_staging.model_candidates, read-only)")
        except Exception as exc:
            log["errors"].append("PILOT_BASELINE_READ_FAILED:" + type(exc).__name__)

    key = os.environ.get("GEMINI_API_KEY")
    if not pf["fits_cap"]:
        log["errors"].append("PREFLIGHT_OVER_CAP: no paid calls made")
    elif not key:
        log["errors"].append("GEMINI_API_KEY_MISSING: no paid calls made")
    else:
        ledger = BudgetLedger(api_key=key, model=MODEL, cap_usd=CAP_USD, max_calls=MAX_CALLS,
                              max_input_tokens_per_call=MAX_INPUT_TOKENS_PER_CALL, max_output_tokens=MAX_OUTPUT_TOKENS)
        ledger.spent_usd = PRIOR_SPEND_USD  # the cap is cumulative across runs of this series
        steps = [("e1-fixtures-gemini-direct", fixtures, lambda: GeminiDirectExtractor(
                      model=MODEL, max_output_tokens=MAX_OUTPUT_TOKENS, transport=budgeted_transport(ledger, "e1"))),
                 ("e2-fixtures-langextract", fixtures, lambda: LangExtractExtractor(
                      max_char_buffer=LX_BUFFER, model=budgeted_langextract_model(ledger, "e2", MAX_OUTPUT_TOKENS),
                      model_label=MODEL + " via budgeted provider"))]
        if pac3:
            steps += [("e3b-pac3-gemini-direct", [pac3], lambda: GeminiDirectExtractor(
                          model=MODEL, max_output_tokens=MAX_OUTPUT_TOKENS, transport=budgeted_transport(ledger, "e3b"))),
                      ("e3c-pac3-langextract", [pac3], lambda: LangExtractExtractor(
                          max_char_buffer=LX_BUFFER, model=budgeted_langextract_model(ledger, "e3c", MAX_OUTPUT_TOKENS),
                          model_label=MODEL + " via budgeted provider"))]
        for label, docs, make in steps:
            if ledger.stopped:
                log["errors"].append(f"{label}:SKIPPED_LEDGER_STOPPED:{ledger.stopped}")
                continue
            run_one(label, docs, make(), method=label.split("-", 2)[-1], model=MODEL)
        log["ledger"] = ledger.report()
        log["ledger_calls"] = ledger.calls
    log["total_seconds"] = round(time.perf_counter() - t0, 2)
    (OUT / "series-log.json").write_text(json.dumps(log, indent=2, ensure_ascii=False, default=str))
    print(json.dumps({k: log[k] for k in ("ledger", "errors", "total_seconds") if k in log}, indent=2, default=str))
    bad = [e for e in log["experiments"].values() if e["validation_problems"] or not e["metrics"]["all_hold"]]
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
