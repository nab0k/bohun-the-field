"""Conservative, P2-only evidence-risk flags. NOT factual verification or publication gating."""
from __future__ import annotations

from collections.abc import Mapping
import re

_PERCENT = re.compile(r"(?<![\w.])(\d{1,3}(?:[,.]\d+)?)\s*(?:%|percent(?:age)?\b|per\s*cent\b|prozent\b)", re.I)
_CONTEXT = re.compile(r"\b(?:acquisition|acquire|purchase|buy|bought|stake|share|shares|equity|erwerb|anteile|anteil|beteiligung)\b", re.I)
_ACTOR = re.compile(r"\b[A-Z][A-Z0-9]{2,}\b")
_IGNORE = frozenset({"THE", "AND", "FOR", "USD", "USA", "GOV", "NATO", "HMS", "EUR", "CEO"})
_US_GOV = re.compile(r"\b(?:(?:US|U\.S\.|United\s+States|USA|American)\s+(?:federal\s+)?government|government\s+of\s+the\s+United\s+States)\b", re.I)
_SHIP = re.compile(r"\bHMS\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3})")
_EXERCISE = re.compile(r"\b(?:Exercise|Exercice)\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*){0,3})")
_WORD = re.compile(r"[\w]+", re.UNICODE)


def _percent(text: str) -> set[float]:
    return {num for match in _PERCENT.finditer(text)
            if 0 <= (num := float(match.group(1).replace(",", "."))) <= 100}


def _shared_actor(a: str, b: str) -> bool:
    return bool((set(_ACTOR.findall(a)) - _IGNORE) &
                (set(_ACTOR.findall(b)) - _IGNORE))


def _normalized(text: str) -> str:
    return " ".join(token.casefold() for token in _WORD.findall(text))


def check_claim_p2(claim_text: str, spans) -> dict:
    """Diagnostics only: never assert entailment, contradiction or truth."""
    if not isinstance(claim_text, str) or not 1 <= len(claim_text) <= 3000:
        raise ValueError("invalid_claim_text")
    if isinstance(spans, (str, bytes, Mapping)):
        raise ValueError("invalid_spans")
    try:
        evidence = list(spans)
    except TypeError:
        raise ValueError("invalid_spans") from None
    if not 1 <= len(evidence) <= 10:
        raise ValueError("invalid_span_count")
    seen = set()
    for row in evidence:
        if (not isinstance(row, Mapping) or set(row) != {"span_id", "text"}
                or not isinstance(row["span_id"], str) or not row["span_id"]
                or row["span_id"] in seen or not isinstance(row["text"], str)
                or not 1 <= len(row["text"]) <= 200):
            raise ValueError("invalid_span")
        seen.add(row["span_id"])

    flags = []
    def flag(code: str, ids=()):
        flags.append({"code": code, "level": "review_required",
                      "evidence_span_ids": list(ids)})

    pct = _percent(claim_text)
    if pct and _CONTEXT.search(claim_text):
        anchored = [s for s in evidence if _CONTEXT.search(s["text"])
                    and _shared_actor(claim_text, s["text"])]
        matches = any(pct & _percent(s["text"]) for s in anchored)
        conflicts = [s["span_id"] for s in anchored
                     if _percent(s["text"]) and pct.isdisjoint(_percent(s["text"]))]
        if conflicts and not matches:
            flag("NUMERIC_PERCENT_CONFLICT_POSSIBLE", conflicts)

    combined = "\n".join(s["text"] for s in evidence)
    if _US_GOV.search(claim_text) and not _US_GOV.search(combined):
        flag("US_GOVERNMENT_ATTRIBUTION_NOT_EXPLICIT")
    norm = _normalized(combined)
    for regex, code in ((_SHIP, "NAMED_SHIP_NOT_EXPLICIT"),
                        (_EXERCISE, "NAMED_EXERCISE_NOT_EXPLICIT")):
        if any(_normalized(m.group(0)) not in norm for m in regex.finditer(claim_text)):
            flag(code)
    return {"schema": "bohun-c3-10-p2-evidence-guard-v1",
            "evidence_basis": "supplied_P2_spans_only",
            "source_authenticity_attested": False,
            "publisher_release_authorized": False,
            "semantic_entailment_attested": False,
            "finding_count": len(flags), "findings": flags}
