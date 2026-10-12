"""Metrics. Quality numbers are computed ONLY against gold labels; otherwise NOT MEASURED.

Matching rule (stated, not novel): a predicted record matches a gold claim when
both are aligned in the same text and their character spans have IoU >= 0.5.
Each gold claim is matched at most once (greedy by IoU).
"""
from .record import find_all

NM = "NOT MEASURED"


def _iou(a, b):
    inter = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


def _norm(x):
    return (x or "").strip().lower() or None


def score_against_gold(records, doc):
    gold = doc.get("gold")
    if not gold:
        return None
    text = doc["text"]
    gold_spans = []
    for g in gold:
        starts = find_all(text, g["evidence_text"])
        if len(starts) != 1:
            raise ValueError("GOLD_SPAN_NOT_UNIQUE:" + g["evidence_text"][:40])
        gold_spans.append((starts[0], starts[0] + len(g["evidence_text"])))
    pairs = []
    for i, r in enumerate(records):
        if r.get("char_start") is None:
            continue
        for j, gs in enumerate(gold_spans):
            iou = _iou((r["char_start"], r["char_end"]), gs)
            if iou >= 0.5:
                pairs.append((iou, i, j))
    used_r, used_g, matches = set(), set(), []
    for iou, i, j in sorted(pairs, reverse=True):
        if i in used_r or j in used_g:
            continue
        used_r.add(i)
        used_g.add(j)
        matches.append((records[i], gold[j]))
    essential = [j for j, g in enumerate(gold) if g.get("essential", True)]
    return {
        "predicted": len(records),
        "gold": len(gold),
        "matched": len(matches),
        "tp_essential": sum(1 for j in used_g if j in essential),
        "essential": len(essential),
        "attribution_correct": sum(_norm(r["asserted_by"]) == _norm(g.get("asserted_by")) for r, g in matches),
        "modality_correct": sum(r["modality"] == g.get("modality") for r, g in matches),
        "matched_detail": [{"gold": g["evidence_text"][:80], "pred_modality": r["modality"],
                            "gold_modality": g.get("modality"), "pred_asserted_by": r["asserted_by"],
                            "gold_asserted_by": g.get("asserted_by")} for r, g in matches],
    }


def aggregate(records, per_doc_gold):
    scored = [r for r in records if r.get("entailment_label")]
    out = {
        "records": len(records),
        "span_not_in_text_rate": _rate(sum("SPAN_NOT_IN_STORED_TEXT" in r["hold_reasons"] for r in records), len(records)),
        "unsupported_claim_rate_minicheck": _rate(sum(r["entailment_label"] == "NOT_SUPPORTED" for r in scored), len(scored)) if scored else NM,
        "all_hold": all(r["verification_status"] == "HOLD" and r["public_release"] is False for r in records),
    }
    g = [x for x in per_doc_gold if x]
    if not g:
        out.update({"precision_span_match": NM, "recall_essential": NM, "attribution_accuracy": NM, "modality_accuracy": NM,
                    "quality_note": "No gold labels for these documents; quality metrics are not computed."})
        return out
    pred = sum(x["predicted"] for x in g)
    matched = sum(x["matched"] for x in g)
    out.update({
        "precision_span_match": _rate(matched, pred),
        "recall_essential": _rate(sum(x["tp_essential"] for x in g), sum(x["essential"] for x in g)),
        "attribution_accuracy": _rate(sum(x["attribution_correct"] for x in g), matched),
        "modality_accuracy": _rate(sum(x["modality_correct"] for x in g), matched),
        "quality_note": f"Computed on {len(g)} gold-labelled document(s). Small samples: not representative.",
    })
    return out


def _rate(num, den):
    return round(num / den, 4) if den else NM


def project_costs(*, prompt_tokens_per_doc, output_tokens_per_doc, price_in_per_m, price_out_per_m,
                  entailment_cpu_s_per_doc, runner_cpu_cores=4, volumes=(1_000, 10_000, 100_000)):
    """Linear projection. Every input is an explicit assumption or a measured value."""
    rows = []
    for n in volumes:
        llm = n * (prompt_tokens_per_doc * price_in_per_m + output_tokens_per_doc * price_out_per_m) / 1_000_000
        cpu_h = n * entailment_cpu_s_per_doc / 3600
        rows.append({
            "docs_per_day": n,
            "llm_usd_per_day": round(llm, 2),
            "llm_usd_per_30d": round(llm * 30, 2),
            "entailment_runner_hours_per_day": round(cpu_h, 2),
            "parallel_runners_needed_for_24h_window": round(cpu_h / 24, 2),
        })
    return rows
