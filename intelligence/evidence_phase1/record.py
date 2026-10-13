"""Evidence record: exact-span alignment against one stored text version.

The record proves only that `evidence_text` occurs at [char_start, char_end) of
the stored text whose SHA-256 is `document_hash`. It does not prove that the
claim follows from the text (see entailment.py) or that the claim is true.
"""
import datetime as dt
import hashlib

MODALITIES = {
    "occurred",    # source states it happened
    "announced",   # an actor states it / announces it; not independently shown
    "planned",     # plan, intention, expectation, future tense
    "negated",     # source states it did not happen / denies it
    "cancelled",   # decided, then cancelled / terminated / withdrawn
    "speculative", # may, could, reportedly, sources suggest
    "unknown",
}

HOLD = "HOLD"


class RecordError(ValueError):
    pass


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_all(text, span):
    """All start offsets of `span` in `text` (overlapping allowed)."""
    if not span:
        return []
    starts, i = [], text.find(span)
    while i != -1:
        starts.append(i)
        i = text.find(span, i + 1)
    return starts


def align_exact(text, span, hint_start=None):
    """Return (start, end, status, n_occurrences).

    status: EXACT_UNIQUE, EXACT_AMBIGUOUS (several occurrences; the one nearest
    to hint_start, else the first, is used), or NOT_FOUND. No fuzzy matching:
    a span that is not byte-identical in the stored text is not evidence.
    """
    if not isinstance(span, str) or not span.strip():
        return None, None, "EMPTY_SPAN", 0
    starts = find_all(text, span)
    if not starts:
        return None, None, "NOT_FOUND", 0
    if len(starts) == 1:
        return starts[0], starts[0] + len(span), "EXACT_UNIQUE", 1
    pick = starts[0] if hint_start is None else min(starts, key=lambda s: abs(s - hint_start))
    return pick, pick + len(span), "EXACT_AMBIGUOUS", len(starts)


def build_record(*, document, candidate, extractor_name, now=None):
    """Turn one extractor candidate into an evidence record (always HOLD).

    document: {source_url, document_id, text, retrieved_at, publication_date}
    candidate: {claim_text, evidence_text, char_start?, char_end?, asserted_by?,
                modality?, event_date?, extractor_alignment?}
    """
    text = document["text"]
    hold_reasons = []
    span = candidate.get("evidence_text") or ""
    hinted = candidate.get("char_start")
    start = end = None
    alignment = "NOT_FOUND"
    occurrences = 0

    # Offsets proposed by an extractor are accepted only if they slice exactly
    # the proposed span from the stored text; otherwise re-align from scratch.
    if isinstance(hinted, int) and isinstance(candidate.get("char_end"), int):
        if 0 <= hinted <= candidate["char_end"] <= len(text) and text[hinted:candidate["char_end"]] == span and span:
            start, end = hinted, candidate["char_end"]
            occurrences = len(find_all(text, span))
            alignment = "EXACT_UNIQUE" if occurrences == 1 else "EXACT_AMBIGUOUS"
        else:
            hold_reasons.append("EXTRACTOR_OFFSETS_DO_NOT_MATCH_SPAN")
    if start is None:
        start, end, alignment, occurrences = align_exact(text, span, hinted if isinstance(hinted, int) else None)
    if alignment in {"NOT_FOUND", "EMPTY_SPAN"}:
        hold_reasons.append("SPAN_NOT_IN_STORED_TEXT" if alignment == "NOT_FOUND" else "EMPTY_SPAN")
    if alignment == "EXACT_AMBIGUOUS":
        hold_reasons.append("SPAN_OCCURS_MULTIPLE_TIMES")
    if candidate.get("extractor_alignment") not in (None, "match_exact"):
        hold_reasons.append("EXTRACTOR_ALIGNMENT_NOT_EXACT:" + str(candidate.get("extractor_alignment")))

    modality = candidate.get("modality") or "unknown"
    if modality not in MODALITIES:
        hold_reasons.append("MODALITY_NOT_IN_VOCABULARY:" + str(modality))
        modality = "unknown"
    asserted_by = candidate.get("asserted_by") or None
    if isinstance(asserted_by, str) and asserted_by.strip().lower() in {"", "unknown", "none", "null", "n/a"}:
        asserted_by = None

    record = {
        "source_url": document["source_url"],
        "document_id": document["document_id"],
        "document_hash": sha256_text(text),
        "retrieved_at": document.get("retrieved_at"),
        # Publication date comes from source metadata only; never from the claim.
        "publication_date": document.get("publication_date"),
        "claim_text": (candidate.get("claim_text") or "").strip(),
        "evidence_text": text[start:end] if start is not None else span,
        "char_start": start,
        "char_end": end,
        "alignment_status": alignment,
        "span_occurrences": occurrences,
        "asserted_by": asserted_by,
        "modality": modality,
        # Filled only by guards.ground_event_date(); the raw model value is kept apart.
        "event_date": None,
        "event_date_candidate": candidate.get("event_date") or None,
        "extractor": extractor_name,
        "entailment_model": None,
        "entailment_score": None,
        "entailment_label": None,
        "hold_reasons": hold_reasons,
        "verification_status": HOLD,
        "public_release": False,
        "created_at": (now or dt.datetime.now(dt.timezone.utc)).isoformat(),
    }
    if not record["claim_text"]:
        hold_reasons.append("EMPTY_CLAIM_TEXT")
    return record


def validate_record(record, text):
    """Independent re-check used by tests and by the readback step."""
    problems = []
    if record.get("verification_status") != HOLD:
        problems.append("STATUS_NOT_HOLD")
    if record.get("public_release") is not False:
        problems.append("PUBLIC_RELEASE_NOT_FALSE")
    if record.get("document_hash") != sha256_text(text):
        problems.append("DOCUMENT_HASH_MISMATCH")
    s, e = record.get("char_start"), record.get("char_end")
    if s is not None:
        if not (isinstance(s, int) and isinstance(e, int) and 0 <= s <= e <= len(text)):
            problems.append("OFFSETS_OUT_OF_RANGE")
        elif text[s:e] != record.get("evidence_text"):
            problems.append("EVIDENCE_TEXT_NOT_AT_OFFSETS")
    elif "SPAN_NOT_IN_STORED_TEXT" not in record.get("hold_reasons", []) and "EMPTY_SPAN" not in record.get("hold_reasons", []):
        problems.append("UNALIGNED_RECORD_WITHOUT_HOLD_REASON")
    if record.get("modality") not in MODALITIES:
        problems.append("BAD_MODALITY")
    if record.get("event_date") and record.get("event_date") == record.get("publication_date") \
            and "EVENT_DATE_GROUNDED" not in record.get("checks", {}).get("event_date", ""):
        problems.append("EVENT_DATE_COPIED_FROM_PUBLICATION_DATE")
    return problems
