"""Semantic entailment: does claim_text follow from the cited evidence context?

Scorer: MiniCheck (Tang, Laban, Durrett, EMNLP 2024), installed from
github.com/Liyan06/MiniCheck at a pinned commit. NOTE: the PyPI package named
`minicheck` is an unrelated project (it depends on z3-solver); do not
`pip install minicheck`.

Grounding document passed to the scorer = the context window around the exact
span (guards.context_window), not the whole article: the question is whether the
claim follows from the cited evidence. The model's own label (support prob > 0.5)
is recorded; no Bohun threshold is claimed calibrated. Entailment never releases
a record: at best it removes one hold reason. Model trained/evaluated on English.
"""
import re
import time

from .guards import context_window

MINICHECK_MODELS = {"flan-t5-large": "lytang/MiniCheck-Flan-T5-Large"}


class EntailmentUnavailable(RuntimeError):
    pass


class MiniCheckScorer:
    def __init__(self, model_name="flan-t5-large", cache_dir="./ckpts", backend=None):
        self.model_name = model_name
        self.label = f"MiniCheck/{MINICHECK_MODELS.get(model_name, model_name)}"
        if backend is not None:  # tests inject a fake with .score(docs, claims)
            self._scorer = backend
            return
        try:
            from minicheck.minicheck import MiniCheck  # git install, see module docstring
        except ImportError as exc:
            raise EntailmentUnavailable("MINICHECK_NOT_INSTALLED") from exc
        t0 = time.perf_counter()
        try:
            self._scorer = MiniCheck(model_name=model_name, cache_dir=cache_dir)
        except Exception as exc:  # e.g. model hub unreachable: report, do not crash the run
            raise EntailmentUnavailable("MINICHECK_LOAD_FAILED:" + type(exc).__name__) from exc
        self.load_seconds = round(time.perf_counter() - t0, 2)

    def score(self, docs, claims):
        out = self._scorer.score(docs=docs, claims=claims)
        labels, probs = out[0], out[1]
        return [(int(l), float(p)) for l, p in zip(labels, probs)]


def apply_entailment(records, texts_by_doc, scorer, batch_size=16):
    """Score aligned records in batches; unaligned or failed ones get a hold reason."""
    todo = []
    for r in records:
        r.setdefault("checks", {})
        if r.get("char_start") is None or not r.get("claim_text"):
            r["hold_reasons"].append("ENTAILMENT_SKIPPED_NO_ALIGNED_EVIDENCE")
            continue
        if len(re.findall(r"[.!?](\s|$)", r["claim_text"].strip())) > 1:
            r["hold_reasons"].append("CLAIM_HAS_MULTIPLE_SENTENCES")
        ctx = context_window(texts_by_doc[r["document_id"]], r["char_start"], r["char_end"])
        r["checks"]["entailment_context_chars"] = len(ctx)
        todo.append((r, ctx))
    if scorer is None:
        for r, _ in todo:
            r["hold_reasons"].append("ENTAILMENT_NOT_RUN")
        return {"scored": 0, "seconds": 0.0}
    t0 = time.perf_counter()
    scored = 0
    for i in range(0, len(todo), batch_size):
        chunk = todo[i:i + batch_size]
        try:
            results = scorer.score([c for _, c in chunk], [r["claim_text"] for r, _ in chunk])
        except Exception as exc:  # fail closed: any scorer error leaves HOLD with a reason
            for r, _ in chunk:
                r["hold_reasons"].append("ENTAILMENT_ERROR:" + type(exc).__name__)
            continue
        for (r, _), (label, prob) in zip(chunk, results):
            r["entailment_model"] = scorer.label
            r["entailment_score"] = round(prob, 4)
            r["entailment_label"] = "SUPPORTED" if label == 1 else "NOT_SUPPORTED"
            if label != 1:
                r["hold_reasons"].append("ENTAILMENT_NOT_SUPPORTED")
            scored += 1
    return {"scored": scored, "seconds": round(time.perf_counter() - t0, 3)}
