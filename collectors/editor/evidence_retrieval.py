"""Bounded, literal P2 excerpt retrieval for C3-10's untrusted shadow checker.

Word and number overlap is a search heuristic, never proof of a claim.
The caller must still verify source lineage and treat model output as untrusted.
"""
from __future__ import annotations

import re


def _words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[^\W_]{3,}", text, flags=re.UNICODE)}


def select_excerpts(body: str, claims: list[dict], *, max_spans: int = 10) -> list[str]:
    """Select literal excerpts: one candidate per claim, then optional context.

    Never manufacture a quotation, merge noncontiguous text, or infer support.
    Extra snippets can expose qualifications or conflicting passages that a
    single keyword-best window misses. Both rounds honor the shared cap.
    """
    if not isinstance(body, str) or not 30 <= len(body) <= 15000:
        raise ValueError("invalid_capture_length")
    if type(max_spans) is not int or not 1 <= max_spans <= 10:
        raise ValueError("invalid_span_limit")
    if not isinstance(claims, list) or not 1 <= len(claims) <= 5 or any(
        not isinstance(c, dict) or not isinstance(c.get("text"), str)
        for c in claims
    ):
        raise ValueError("invalid_claims")

    windows = []
    for paragraph in re.split(r"(?<=[.!?])\s+|\n+", body):
        paragraph = paragraph.strip()
        if len(paragraph) < 35:
            continue
        for start in range(0, len(paragraph), 180):
            quote = paragraph[start:start + 200]
            if len(quote) >= 25 and quote.strip() and quote not in windows:
                windows.append(quote)
    if not windows:
        raise ValueError("no_citable_excerpts")

    ranked_by_claim = []
    for claim in claims:
        words = _words(claim["text"])
        nums = set(re.findall(r"\d+(?:[.,]\d+)?", claim["text"]))
        scored = []
        for position, quote in enumerate(windows):
            overlap_words = len(words & _words(quote))
            overlap_nums = len(nums & set(re.findall(r"\d+(?:[.,]\d+)?", quote)))
            score = overlap_words + 3 * overlap_nums
            if score >= 2:
                scored.append((-score, position, quote))
        ranked_by_claim.append([quote for _, _, quote in sorted(scored)])

    selected: list[str] = []
    seen: set[str] = set()
    # Coverage-first: choose each claim's best lexical candidate before adding
    # a second snippet for any claim. A shared quote consumes only one span.
    for ranked in ranked_by_claim:
        if ranked and ranked[0] not in seen:
            selected.append(ranked[0])
            seen.add(ranked[0])
        if len(selected) >= max_spans:
            return selected

    # One different lexical candidate per claim, if available. These passages
    # might provide missing qualifiers but are NOT verified entailment.
    for ranked in ranked_by_claim:
        for quote in ranked[1:]:
            if quote not in seen:
                selected.append(quote)
                seen.add(quote)
                break
        if len(selected) >= max_spans:
            return selected

    if not selected:
        raise ValueError("no_lexically_related_excerpt")
    return selected
