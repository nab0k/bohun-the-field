#!/usr/bin/env python3
"""Print run results as GitHub Actions ::notice annotations (readable via the Checks API).

Only metrics and compact per-record fields; evidence spans are cut to 160 chars.
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "output/evidence-phase1"


def esc(s):
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def notice(title, payload):
    print(f"::notice title={title}::" + esc(json.dumps(payload, ensure_ascii=False)))


def main(label):
    summary = json.loads((OUT / f"{label}-summary.json").read_text())
    notice(f"{label}-summary", {k: summary.get(k) for k in ("metrics", "entailment_run", "resources", "measurements", "run")}
           | {"validation_problems": len(summary.get("validation_problems", [])),
              "documents": [{k: d.get(k) for k in ("document_id", "outcome", "candidates", "text_chars", "extraction_meta", "gold_eval")}
                            for d in summary["documents"]]})
    time_file = OUT / f"{label}-time.txt"
    if time_file.exists():
        lines = [l.strip() for l in time_file.read_text().splitlines()
                 if any(k in l for k in ("Maximum resident", "Elapsed", "User time", "System time"))]
        notice(f"{label}-time", lines)
    rows = []
    for line in (OUT / f"{label}-records.jsonl").read_text().splitlines():
        r = json.loads(line)
        rows.append({"doc": r["document_id"][-28:], "claim": r["claim_text"][:160], "evidence": (r["evidence_text"] or "")[:160],
                     "chars": [r["char_start"], r["char_end"]], "modality": r["modality"], "asserted_by": r["asserted_by"],
                     "event_date": r["event_date"], "ent": [r["entailment_label"], r["entailment_score"]],
                     "hold": r["hold_reasons"]})
    for i in range(0, len(rows), 6):  # GitHub keeps up to 10 notices per step
        notice(f"{label}-records-{i // 6 + 1}", rows[i:i + 6])


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "experiment")
