"""Orchestration: documents -> candidates -> records -> guards -> entailment -> metrics."""
import resource
import time

from . import PIPELINE_VERSION
from .entailment import apply_entailment
from .guards import apply_guards
from .metrics import aggregate, score_against_gold
from .record import build_record, validate_record

HARD_MAX_DOCS = 10  # cost guard for any single run


class ReplayExtractor:
    """Deterministic extractor that replays recorded candidates (tests / re-runs)."""
    name = "replay"

    def __init__(self, by_document_id):
        self.by_document_id = by_document_id
        self._current = None

    def for_document(self, document_id):
        self._current = document_id
        return self

    def extract(self, text):
        return {"candidates": [dict(c) for c in self.by_document_id.get(self._current, [])],
                "meta": {"extractor": self.name, "latency_s": 0.0, "token_usage_source": "NOT_APPLICABLE",
                         "prompt_tokens": None, "output_tokens": None, "input_chars": len(text)}}


def run(documents, extractor, scorer=None, max_docs=3, wide_context=True):
    if max_docs > HARD_MAX_DOCS:
        raise ValueError(f"max_docs>{HARD_MAX_DOCS} needs separate approval")
    t_start = time.perf_counter()
    documents = documents[:max_docs]
    records, doc_results, per_doc_gold = [], [], []
    texts = {d["document_id"]: d["text"] for d in documents}
    for doc in documents:
        res = {"document_id": doc["document_id"], "source_url": doc["source_url"],
               "provenance": doc.get("provenance"), "text_status": doc["text_status"],
               "text_chars": len(doc["text"]), "text_extraction": doc.get("text_extraction")}
        if doc["text_status"] != "TEXT_PRESENT_COMPLETENESS_UNVERIFIED":
            res["outcome"] = "SKIPPED_NO_FULL_TEXT"
            doc_results.append(res)
            per_doc_gold.append(None)
            continue
        ex = extractor.for_document(doc["document_id"]) if hasattr(extractor, "for_document") else extractor
        try:
            out = ex.extract(doc["text"])
        except Exception as exc:  # provider error, quota, bad JSON: fail closed, no records
            res["outcome"] = "EXTRACTION_FAILED:" + type(exc).__name__ + ":" + str(exc)[:400]
            doc_results.append(res)
            per_doc_gold.append(None)
            continue
        res["extraction_meta"] = out["meta"]
        doc_records = []
        for cand in out["candidates"]:
            rec = build_record(document=doc, candidate=cand, extractor_name=out["meta"]["extractor"])
            apply_guards(rec, doc["text"])
            doc_records.append(rec)
        res["outcome"] = "EXTRACTED"
        res["candidates"] = len(doc_records)
        records.extend(doc_records)
        doc_results.append(res)
        per_doc_gold.append((doc, doc_records))
    ent = apply_entailment(records, texts, scorer, wide_context=wide_context)
    gold_scores = []
    for item, res in zip(per_doc_gold, doc_results):
        if item is None:
            gold_scores.append(None)
            continue
        doc, doc_records = item
        g = score_against_gold(doc_records, doc)
        res["gold_eval"] = g
        gold_scores.append(g)
    problems = []
    for r in records:
        for p in validate_record(r, texts[r["document_id"]]):
            problems.append({"document_id": r["document_id"], "problem": p})
    return {
        "pipeline_version": PIPELINE_VERSION,
        "documents": doc_results,
        "records": records,
        "metrics": aggregate(records, gold_scores),
        "entailment_run": ent,
        "validation_problems": problems,
        "resources": {"wall_s": round(time.perf_counter() - t_start, 3),
                      "max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                      "cpu_user_s": round(resource.getrusage(resource.RUSAGE_SELF).ru_utime, 2)},
    }
