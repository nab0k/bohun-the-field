#!/usr/bin/env python3
"""Bohun Evidence Pipeline, Phase 1 — experimental runner (HOLD only, no writes).

Examples (see docs/EVIDENCE_PHASE1.md):
  python scripts/evidence-phase1-experiment.py --source fixtures --extractor replay
  python scripts/evidence-phase1-experiment.py --source fixtures --extractor gemini-direct --entailment minicheck
  python scripts/evidence-phase1-experiment.py --source staging --title-like "PAC-3" --extractor langextract
  python scripts/evidence-phase1-experiment.py --source pdf --pdf-url https://... --extractor gemini-direct

Outputs go to output/evidence-phase1/. They contain claim records with short
evidence spans, hashes and metrics, but never the full source text.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from intelligence.evidence_phase1 import documents as D  # noqa: E402
from intelligence.evidence_phase1.pipeline import ReplayExtractor, run  # noqa: E402

OUT = ROOT / "output/evidence-phase1"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["fixtures", "staging", "pdf"], required=True)
    ap.add_argument("--fixtures", nargs="*")
    ap.add_argument("--title-like")
    ap.add_argument("--pdf-url")
    ap.add_argument("--extractor", choices=["replay", "gemini-direct", "langextract"], required=True)
    ap.add_argument("--replay-file", default=str(ROOT / "intelligence/fixtures/evidence_phase1/replay/candidates.json"))
    ap.add_argument("--entailment", choices=["none", "minicheck"], default="none")
    ap.add_argument("--max-docs", type=int, default=3)
    ap.add_argument("--label", default="run")
    a = ap.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    measurements = {}
    if a.source == "fixtures":
        docs = D.load_fixtures(a.fixtures)
    elif a.source == "staging":
        docs = D.load_staging(limit=a.max_docs, title_like=a.title_like)
    else:
        doc, measurements["docling"] = D.load_pdf_with_docling(a.pdf_url, OUT / "pdf-work")
        docs = [doc]

    if a.extractor == "replay":
        extractor = ReplayExtractor(json.loads(Path(a.replay_file).read_text()))
    elif a.extractor == "gemini-direct":
        from intelligence.evidence_phase1.extractors import GeminiDirectExtractor
        extractor = GeminiDirectExtractor()
    else:
        from intelligence.evidence_phase1.extractors import LangExtractExtractor
        extractor = LangExtractExtractor()

    scorer = None
    if a.entailment == "minicheck":
        from intelligence.evidence_phase1.entailment import EntailmentUnavailable, MiniCheckScorer
        try:
            scorer = MiniCheckScorer(cache_dir=str(ROOT / "ckpts"))
            measurements["minicheck_load_s"] = getattr(scorer, "load_seconds", None)
        except EntailmentUnavailable as exc:
            measurements["minicheck"] = "UNAVAILABLE:" + str(exc)

    t0 = time.perf_counter()
    result = run(docs, extractor, scorer, max_docs=a.max_docs)
    result["measurements"] = measurements
    result["run"] = {"label": a.label, "source": a.source, "extractor": a.extractor,
                     "entailment": a.entailment, "seconds": round(time.perf_counter() - t0, 3)}

    with open(OUT / f"{a.label}-records.jsonl", "w", encoding="utf-8") as fh:
        for r in result["records"]:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    summary = {k: v for k, v in result.items() if k != "records"}
    (OUT / f"{a.label}-summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({"label": a.label, "metrics": result["metrics"], "resources": result["resources"],
                      "entailment_run": result["entailment_run"], "measurements": measurements,
                      "validation_problems": len(result["validation_problems"]),
                      "documents": [{k: d.get(k) for k in ("document_id", "outcome", "candidates", "text_chars")}
                                    for d in result["documents"]]}, indent=2, ensure_ascii=False))
    if result["validation_problems"] or not result["metrics"]["all_hold"]:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
