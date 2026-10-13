"""Deterministic HOLD guards (English cue lists, Phase 1).

These rules are NOT a modality/negation classifier and claim no accuracy. They
only look for surface cues around the evidence span that contradict the label
the extractor returned, and add a hold reason when they do. They can never move
a record out of HOLD. Non-English text is reported as NOT_SUPPORTED, not passed.
"""
import calendar
import re

PLAN_CUES = r"\b(will|would|plans? to|planned|intends? to|intended|expects? to|expected to|aims? to|is to|are to|set to|scheduled to|proposed|proposes|seeks? to|could|may|might|potential(ly)?|future)\b"
NEGATION_CUES = r"\b(not|no|never|denied|denies|deny|rejected|refuted|without|neither|nor|did ?n[o']t|has ?n[o']t|have ?n[o']t|was ?n[o']t|is ?n[o']t)\b"
CANCEL_CUES = r"\b(cancel(l)?ed|cancel(l)?ation|terminated|termination|withdr[ae]w[n]?|scrapped|halted|suspended|revoked|rescinded|abandoned)\b"
REPORTED_CUES = r"\b(said|says|according to|claimed|claims|told|announced|stated|reported(ly)?|confirmed|alleged(ly)?|sources?)\b"
SPECULATIVE_CUES = r"\b(reportedly|rumou?red|allegedly|could|may|might|possibly|likely|unconfirmed|is said to)\b"

# Claim-side presence is checked with word stems, so a paraphrase that keeps the meaning
# ("plans a plant" for "plans to build a plant") does not count as dropping the cue.
CLAIM_PLAN_STEMS = r"\b(will|would|shall|plan\w*|intend\w*|intent\w*|expect\w*|aim\w*|target\w*|propos\w*|schedul\w*|set to|seek\w*|could|may|might|potential\w*|future|to be|upcoming|forthcoming)\b"
CLAIM_SPECULATIVE_STEMS = r"\b(may|might|could|report\w*|alleg\w*|possibl\w*|likely|rumou?r\w*|unconfirm\w*|said to|reportedly)\b"
CLAIM_NEGATION_STEMS = r"\b(not|no|never|den(y|ies|ied|ial)\w*|reject\w*|refut\w*|without|neither|nor|\w+n't|yet to|fail\w*)\b"

MONTHS = [calendar.month_name[i] for i in range(1, 13)]


def context_window(text, start, end, radius=400):
    """Sentence-ish window around [start, end): extends to '. ' boundaries within radius."""
    if start is None:
        return ""
    left_floor = max(0, start - radius)
    cut = max(text.rfind(". ", left_floor, start), text.rfind("\n", left_floor, start))
    left = cut + 1 if cut >= 0 else left_floor
    right_cap = min(len(text), end + radius)
    nxt = [i for i in (text.find(". ", end, right_cap), text.find("\n", end, right_cap)) if i != -1]
    right = min(nxt) + 1 if nxt else right_cap
    return text[left:right].strip()


def wide_window(text, start, end, radius=1200):
    """Paragraph-sized window: the sentence window plus neighbouring sentences up to radius."""
    if start is None:
        return ""
    left_floor = max(0, start - radius)
    cut = text.find(". ", left_floor, start)  # first sentence boundary inside the left margin
    left = cut + 2 if cut != -1 and cut + 2 <= start else left_floor
    right_cap = min(len(text), end + radius)
    cut = text.rfind(". ", end, right_cap)  # last sentence boundary inside the right margin
    right = cut + 1 if cut != -1 else right_cap
    return text[left:right].strip()


def _hits(pattern, s):
    """Cue matches, ignoring capitalised words in mid-sentence (parts of proper names such as
    'Future Interceptor'). Added after run 38324082330, where such names caused false PLAN cues."""
    out = []
    for m in re.finditer(pattern, s, flags=re.IGNORECASE):
        word = m.group(0)
        before = s[:m.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?:;\"'“”(\n"
        if word[0].isupper() and not sentence_start:
            continue
        out.append(word.lower())
    return out


def _has(pattern, s):
    return bool(_hits(pattern, s))


def looks_english(text):
    words = re.findall(r"[A-Za-z]+", text.lower())
    if len(words) < 20:
        return None
    common = {"the", "and", "of", "to", "in", "a", "is", "for", "on", "with", "that", "by", "as", "was", "will"}
    return sum(w in common for w in words) / len(words) > 0.12


def date_surface_forms(iso):
    m = re.fullmatch(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", iso or "")
    if not m:
        return "UNPARSEABLE", []
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if d:
        name = MONTHS[int(mo) - 1]
        di = str(int(d))
        return "day", [iso, f"{di} {name} {y}", f"{name} {di}, {y}", f"{name} {di} {y}",
                       f"{d}.{mo}.{y}", f"{d}/{mo}/{y}", f"{name[:3]} {di}, {y}", f"{di} {name[:3]} {y}"]
    if mo:
        name = MONTHS[int(mo) - 1]
        return "month", [f"{y}-{mo}", f"{name} {y}", f"{name[:3]} {y}"]
    return "year", [y]


def ground_event_date(record, window):
    """event_date is set only when the candidate date is visible in the evidence window."""
    cand = record.get("event_date_candidate")
    if not cand:
        record["checks"]["event_date"] = "NO_EVENT_DATE_PROPOSED"
        return
    granularity, forms = date_surface_forms(cand)
    if granularity == "UNPARSEABLE":
        record["hold_reasons"].append("EVENT_DATE_UNPARSEABLE")
        record["checks"]["event_date"] = "UNPARSEABLE"
        return
    if any(f.lower() in window.lower() for f in forms):
        record["event_date"] = cand
        record["checks"]["event_date"] = f"EVENT_DATE_GROUNDED:{granularity}"
        return
    if granularity == "day" and cand == record.get("publication_date"):
        record["hold_reasons"].append("EVENT_DATE_EQUALS_PUBLICATION_DATE_NOT_IN_EVIDENCE")
    else:
        record["hold_reasons"].append("EVENT_DATE_NOT_IN_EVIDENCE")
    record["checks"]["event_date"] = "UNGROUNDED"


def apply_guards(record, text):
    record.setdefault("checks", {})
    window = context_window(text, record.get("char_start"), record.get("char_end"))
    record["checks"]["context_chars"] = len(window)
    lang = looks_english(window if len(window) > 200 else text)
    record["checks"]["english_cues_applicable"] = lang
    if lang is False:
        record["hold_reasons"].append("GUARDS_NOT_SUPPORTED_FOR_LANGUAGE")
    if not window:
        ground_event_date(record, "")
        return record
    span = record["evidence_text"]
    modality = record["modality"]
    if modality in {"occurred", "announced"}:
        if _has(PLAN_CUES, span):
            record["hold_reasons"].append("PLAN_CUE_IN_SPAN_CONFLICTS_WITH_MODALITY")
        if _has(NEGATION_CUES, span):
            record["hold_reasons"].append("NEGATION_CUE_IN_SPAN_CONFLICTS_WITH_MODALITY")
        if _has(CANCEL_CUES, window):
            record["hold_reasons"].append("CANCELLATION_CUE_IN_CONTEXT")
        if modality == "occurred" and _has(SPECULATIVE_CUES, window):
            record["hold_reasons"].append("SPECULATIVE_CUE_IN_CONTEXT")
    if modality == "negated" and not _has(NEGATION_CUES, window):
        record["hold_reasons"].append("NEGATED_WITHOUT_NEGATION_CUE")
    if modality == "cancelled" and not _has(CANCEL_CUES, window):
        record["hold_reasons"].append("CANCELLED_WITHOUT_CANCELLATION_CUE")
    if modality == "planned" and not _has(PLAN_CUES, window):
        record["hold_reasons"].append("PLANNED_WITHOUT_PLAN_CUE")
    # A hedge, plan or negation present in the cited span but absent from the claim is the
    # failure MiniCheck missed in run 38324082330 ("would represent" -> "represents").
    claim = record.get("claim_text") or ""
    for name, pattern, claim_pattern in (("PLAN", PLAN_CUES, CLAIM_PLAN_STEMS),
                                         ("SPECULATIVE", SPECULATIVE_CUES, CLAIM_SPECULATIVE_STEMS),
                                         ("NEGATION", NEGATION_CUES, CLAIM_NEGATION_STEMS)):
        if _has(pattern, span) and not _has(claim_pattern, claim):
            record["hold_reasons"].append(f"CLAIM_DROPS_{name}_CUE_FROM_SPAN")
    if _has(REPORTED_CUES, window) and not record.get("asserted_by"):
        record["hold_reasons"].append("ATTRIBUTION_CUE_BUT_ASSERTED_BY_UNKNOWN")
    if record.get("asserted_by") and record["asserted_by"].lower() not in window.lower():
        record["hold_reasons"].append("ASSERTED_BY_NOT_NAMED_IN_CONTEXT")
    ground_event_date(record, window)
    return record
