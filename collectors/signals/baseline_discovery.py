"""C4-08 baseline discovery, opt-in adapters only.

No database connection, API request, or search service is created here.
The caller supplies a read-only SQL runner and optional HTTP/search transports.
Outputs evidence candidates, never signal decisions or publication permissions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence
import json
import re

USASPENDING_RECIPIENT = "https://api.usaspending.gov/api/v2/search/spending_by_category/recipient/"
MAX_DB_RESULTS = 20
MAX_WEB_REQUESTS = 2
MAX_VERIFICATIONS = 8
LEGAL_FORMS = frozenset({'inc', 'incorporated', 'llc', 'ltd', 'limited', 'corp', 'corporation', 'co', 'company', 'plc'})

@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    entity: str
    metric: str
    unit: str
    kind: str  # rate | cumulative | annual_total | point_in_time
    as_of: str  # ISO date; no future evidence after as_of
    target_value: str = ""
    stage: str = ""  # announced | contracted | delivered | demonstrated
    aliases: tuple[str, ...] = ()

@dataclass
class Evidence:
    provider: str
    value: str | None
    value_date: str | None
    quote: str | None
    url: str | None
    metric: str
    unit: str
    kind: str
    stage: str
    comparable: bool
    explanation: str
    root_source: str | None = None
    verification: str = "candidate"  # verified only when source span actually checked
    first_seen_at: str | None = None  # required before historical baseline acceptance
    subject: str | None = None        # exact organization/object of the measurement
    scope: str | None = None          # denominator/geography/program/period description
    date_precision: str | None = None  # day | month; never invent day of month
    approximate: bool = False
    lower_bound: bool = False

@dataclass(frozen=True)
class PrimaryDocument:
    url: str
    text: str
    first_seen_at: str
    root_source: str
    scope: str
    primary: bool = True
    published_at: str | None = None
    title: str = ""

@dataclass(frozen=True)
class Proposal:
    """Untrusted extraction from caller-supplied model. Not evidence by itself."""
    value: str
    unit: str
    kind: str
    stage: str
    organization: str
    speaker: str
    quote: str
    date_text: str
    is_estimate_or_forecast: bool
    metric: str = ""

@dataclass
class Discovery:
    candidate_id: str
    baseline: Evidence | None = None
    alternatives: list[Evidence] = field(default_factory=list)
    second_source: Evidence | None = None
    verification_count: int = 0
    searched: list[dict[str, str]] = field(default_factory=list)
    outcome: str = "not_found"  # found | needs_verification | not_found
    # No opinion about existence or strength of signal.

def _date(s: str) -> date:
    return date.fromisoformat(s)

def _text(s: str) -> str:
    return " ".join(s.casefold().split())

def _safe_value(value: Any) -> str | None:
    if value is None:
        return None
    try:
        n = Decimal(str(value))
        return format(n.normalize(), "f") if n.is_finite() else None
    except (InvalidOperation, ValueError):
        return None

def _norm_name(value: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", value.casefold())
    while tokens and tokens[-1] in LEGAL_FORMS:
        tokens.pop()
    return " ".join(tokens)

def _names(candidate: Candidate) -> tuple[str, ...]:
    return tuple(dict.fromkeys(n for n in (candidate.entity, *candidate.aliases) if n.strip()))

def _identity(candidate: Candidate, name: str) -> bool:
    return bool(_norm_name(name)) and _norm_name(name) in {_norm_name(n) for n in _names(candidate)}

def _metric_terms(metric: str) -> list[str]:
    return [term for term in re.split(r"[_\W]+", metric) if len(term) > 2]

def _number_in(value: str, content: str) -> bool:
    try:
        expected = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return False
    for text in re.findall(r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?(?![\w.])", content):
        try:
            if Decimal(text.replace(",", "")) == expected:
                return True
        except InvalidOperation:
            continue
    return False


MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH_NAMES = "|".join(MONTHS)
ANALYST_ESTIMATE = re.compile(
    r"\b(?:analysts?|consultants?|experts?|observers?)\b.{0,60}?"
    r"\b(?:estimate|estimates|estimated|forecast|forecasts|predict|predicts|"
    r"believe|believes|expect|expects)\b", re.I)
LOWER_BOUND = re.compile(r"\b(?:over|more than|at least|above|exceed(?:ed|s)?)\b", re.I)
CAPACITY = re.compile(r"\b(?:capacity|capable of|capability|potential|can produce|"
                      r"could produce|maximum output|production capacity)\b", re.I)
NEGATIVE = re.compile(r"\b(?:not|never|no longer|failed to|did not|hasn't|haven't|cannot|can't|without)\b", re.I)
SPECULATIVE = re.compile(
    r"\b(?:hope|hopes|hoped|aim|aims|plan|plans|planned|intend|intends|"
    r"expect|expects|expected|forecast|forecasts|predict|predicts|target|"
    r"targets|goal|goals|could|might|would|will|potential|projected)\b", re.I)
CONTRAST = re.compile(r"\b(?:unlike|rather than|as opposed to|compared with)\b", re.I)
RATE = re.compile(r"(?:/year\b|\bper\s+year\b|\bevery\s+year\b|\bannually\b|\bannual\s+(?:rate|pace)\b)", re.I)
CUMULATIVE = re.compile(r"\b(?:total(?:led)?|cumulative|to date|so far|altogether|in all)\b", re.I)
OUTPUT_ACTION = re.compile(r"\b(?:built|builds|made|makes|produced|produces|manufactured|manufactures|"
                           r"delivered|delivers|shipped|ships|supplied|supplies)\b", re.I)
CONTRACT_ACTION = re.compile(r"\b(?:won|awarded|signed|received|secured)\b", re.I)
APPROX = re.compile(r"\b(?:about|around|approximately|roughly|nearly|circa|estimated|almost)\b|~", re.I)
MISSILE_OBJECT = re.compile(r"\b(?:missile|missiles|essm|interceptor|interceptors)\b", re.I)
VEHICLE_OBJECT = re.compile(r"\b(?:truck|trucks|vehicle|vehicles)\b", re.I)


def _source_date(quote: str) -> tuple[str, str] | None:
    """Return date with explicit precision; never assign a day to a month."""
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", quote)
    if iso:
        try:
            return date.fromisoformat(iso.group(1)).isoformat(), "day"
        except ValueError:
            return None
    plain = re.search(r"\b(" + MONTH_NAMES + r")\s+(\d{1,2}),?\s+(\d{4})\b", quote, re.I)
    if plain:
        try:
            return date(int(plain.group(3)), MONTHS[plain.group(1).lower()],
                        int(plain.group(2))).isoformat(), "day"
        except ValueError:
            return None
    month = re.search(r"\b(" + MONTH_NAMES + r")\s+(\d{4})\b", quote, re.I)
    if month:
        return f"{int(month.group(2)):04d}-{MONTHS[month.group(1).lower()]:02d}", "month"
    return None


def _date_upper_bound(value: str, precision: str | None = None) -> date:
    if precision == "year" or re.fullmatch(r"\d{4}", value):
        return date(int(value), 12, 31)
    if precision == "month" or re.fullmatch(r"\d{4}-\d{2}", value):
        start = date.fromisoformat(value + "-01")
        return (date(start.year + 1, 1, 1) if start.month == 12 else
                date(start.year, start.month + 1, 1)) - timedelta(days=1)
    return date.fromisoformat(value)


def _actor_clause(quote: str, candidate: Candidate) -> str | None:
    """Conservative syntactic proxy: name must be the clause actor, not mere mention."""
    for name in _names(candidate):
        tokens = re.escape(name).replace(r"\ ", r"\s+")
        pattern = re.compile(r"(?<![\w])" + tokens + r"(?![\w])", re.I)
        for match in pattern.finditer(quote):
            # Reject contrastive "Raytheon, unlike ACME, ...", where ACME is not actor.
            before = quote[:match.start()]
            if re.search(r"\b(?:unlike|rather than)\s+$", before, re.I):
                continue
            after = quote[match.end():]
            # The name must lead an action clause. Allow "said in March 2026 that it now produces".
            if re.match(r"\s*(?:,?\s*(?:Inc\.?|LLC|Company)\b[,.]?)?\s*"
                        r"(?:said(?:\s+in\s+[A-Za-z]+\s+\d{4})?\s+that\s+it\s+(?:now\s+)?(?:produces|makes|manufactures)|"
                        r"(?:has\s+)?(?:won|built|made|produced|manufactured|delivered|ships|shipped|"
                        r"produces|makes|manufactures|delivers))\b", after, re.I):
                return quote[match.start():]
    return None


def _parse_quantity(quote: str, candidate: Candidate) -> tuple[str, str, bool] | None:
    """Return normalized numeric value, independent observed unit and approximation.

    Only deliberately supported forms can be verified. Unrecognized prose -> candidate.
    """
    if candidate.metric == "contract_value":
        pattern = re.compile(r"(?<![\w])\$\s*(\d[\d,]*(?:\.\d+)?)\s*"
                             r"(million|billion|thousand)?\b", re.I)
        items = [(Decimal(m.group(1).replace(",", "")) *
                  {"million": Decimal("1000000"), "billion": Decimal("1000000000"),
                   "thousand": Decimal("1000")}.get((m.group(2) or "").lower(), Decimal(1)))
                 for m in pattern.finditer(quote)]
        if len(set(items)) != 1:
            return None
        return _safe_value(items[0]), "USD", bool(APPROX.search(quote))
    # For unit counts, support literally written "100 units/year", "100 missiles per year",
    # or "100 units every year"; do not infer arbitrary conversions.
    pattern = re.compile(r"(?<![\w.])(?:about\s+|around\s+|approximately\s+|roughly\s+)?"
                         r"(\d[\d,]*(?:\.\d+)?)\s*"
                         r"(units|missiles|interceptors)(?:\s*/\s*year|\s+per\s+year|\s+every\s+year)?", re.I)
    candidates = []
    for match in pattern.finditer(quote):
        unit_word = match.group(2).lower()
        period = quote[match.start():match.end()].lower()
        annual = bool(re.search(r"/\s*year|\bper\s+year\b|\bevery\s+year\b", period))
        observed_unit = "missiles/year" if annual and unit_word == "missiles" else (
            "units/year" if annual and unit_word == "units" else
            "interceptors/year" if annual and unit_word == "interceptors" else unit_word)
        candidates.append((_safe_value(match.group(1).replace(",", "")), observed_unit))
    unique = {(v, u) for v, u in candidates if v is not None}
    if len(unique) != 1:
        return None
    value, unit = next(iter(unique))
    return value, unit, bool(APPROX.search(quote))


def _observed_metric_kind(quote: str, candidate: Candidate) -> tuple[str, str, str] | None:
    if NEGATIVE.search(quote) or SPECULATIVE.search(quote) or ANALYST_ESTIMATE.search(quote) or CAPACITY.search(quote):
        return None
    if candidate.metric == "contract_value":
        if not CONTRACT_ACTION.search(quote) or not re.search(r"\bcontract\b", quote, re.I):
            return None
        return "contract_value", "point_in_time", "contracted"
    if candidate.metric == "federal_obligations":
        # Aggregate amounts are not a verified claim without source details.
        return None
    # Avoid confirming missile output from truck production.
    if candidate.metric in {"missile_output", "essm_output"}:
        if not MISSILE_OBJECT.search(quote) or VEHICLE_OBJECT.search(quote):
            return None
    elif candidate.metric in {"units", "unit_output"}:
        if not re.search(r"\bunits\b", quote, re.I):
            return None
    else:
        return None  # unsupported metric: no guessing based on candidate
    if not OUTPUT_ACTION.search(quote):
        return None
    observed_kind = "rate" if RATE.search(quote) else (
        "cumulative" if CUMULATIVE.search(quote) else "point_in_time")
    if candidate.kind == "cumulative" and observed_kind == "point_in_time":
        # Only a dated count explicitly described as cumulative may prove cumulative.
        return None
    stage = "delivered" if re.search(r"\b(?:delivered|shipped|supplied)\b", quote, re.I) else (
        "demonstrated" if re.search(r"\bdemonstrated\b", quote, re.I) else "")
    return candidate.metric, observed_kind, stage


def verify_primary(candidate: Candidate, lead: Evidence, document: PrimaryDocument) -> Evidence:
    """Conservative literal check. No LLM decision and no network access.

    Semantic inference is intentionally limited to explicit supported forms.
    """
    failed = replace(lead, verification="candidate", comparable=False)
    if not document.primary or document.url != lead.url or not document.url.startswith("https://"):
        failed.explanation = "primary_document_mismatch"
        return failed
    if not document.text or not document.root_source:
        failed.explanation = "missing_primary_text_or_root"
        return failed

    # Entire sentence rather than isolated snippets, not source metadata or headline.
    sentences = [line.strip() for line in document.text.splitlines() if line.strip()]
    if lead.quote and lead.quote in document.text:
        # If a lead supplied a factual quote use only the same source sentence,
        # not unrelated clauses in the document.
        sentences = [line for line in sentences if lead.quote in line]

    for quote in sentences:
        actor = _actor_clause(quote, candidate)
        if actor is None:
            continue
        # Restrict semantic claims to the actor's clause. Do not borrow a number
        # belonging to another company mentioned earlier or later.
        clause = actor.split(". ", 1)[0]
        if (CONTRAST.search(quote) or NEGATIVE.search(quote) or
                SPECULATIVE.search(quote) or ANALYST_ESTIMATE.search(quote) or
                CAPACITY.search(quote)):
            continue
        observed = _observed_metric_kind(clause, candidate)
        if observed is None:
            continue
        observed_metric, observed_kind, observed_stage = observed
        if observed_kind != candidate.kind:
            continue
        if candidate.stage and observed_stage != candidate.stage:
            continue
        parsed_quantity = _parse_quantity(clause, candidate)
        if parsed_quantity is None:
            continue
        value, observed_unit, approximate = parsed_quantity
        if _text(observed_unit) != _text(candidate.unit):
            continue
        if lead.value is not None and _safe_value(lead.value) != value:
            continue
        # Dates commonly prefix the actor: 'On 2026-01-01 ACME produced ...'.
        # Keep that temporal preamble while claims/numbers stay actor-scoped.
        parsed_date = _source_date(quote)
        if parsed_date is None:
            continue
        value_date, precision = parsed_date
        if lead.value_date is not None and lead.value_date != value_date:
            continue

        checked = replace(
            lead, value=value, value_date=value_date, date_precision=precision,
            approximate=approximate, quote=quote, unit=observed_unit,
            metric=observed_metric, kind=observed_kind, stage=observed_stage,
            first_seen_at=document.first_seen_at, root_source=document.root_source,
            subject=candidate.entity, scope=document.scope,
            verification="verified", comparable=False)
        matches, reason = _compatible(candidate, checked)
        if matches:
            checked.comparable = True
            checked.explanation = "verified_explicit_actor_metric_quantity_and_date"
            return checked
        failed.explanation = reason
    if failed.explanation == lead.explanation:
        failed.explanation = "claim_not_explicitly_supported_by_primary_text"
    return failed


def _proposed_date(text: str, document: PrimaryDocument) -> tuple[str, str] | None:
    """Relative years require actual publication date; preserve year precision."""
    raw = text.strip()
    relative = re.fullmatch(r"(last year|this year)", raw, re.I)
    if relative:
        if not document.published_at:
            return None
        try:
            publication = _date(document.published_at)
        except (TypeError, ValueError):
            return None
        year = publication.year - (1 if relative.group(1).lower() == "last year" else 0)
        return str(year), "year"
    return _source_date(raw)


def _same_normalized_value(a: str, b: str) -> bool:
    return _safe_value(a) is not None and _safe_value(a) == _safe_value(b)


def _proposal_subject(candidate: Candidate, proposal: Proposal,
                      document: PrimaryDocument, paragraph: str) -> bool:
    """The named actor or an explicitly affiliated first-person speaker."""
    if not _identity(candidate, proposal.organization):
        return False
    names = _names(candidate)
    quote = proposal.quote
    if any(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", quote, re.I)
           for name in names):
        return _actor_clause(quote, candidate) is not None
    if not re.search(r"\b(?:we|our)\b", quote, re.I):
        return False
    speaker = proposal.speaker.strip()
    if not speaker:
        return False
    role = r"(?:vice president|president|executive|director|CEO|manager|head|spokesperson)"
    for name in names:
        company = re.escape(name)
        person = re.escape(speaker)
        # Only a concrete affiliation attached to the claimed speaker is enough.
        # Mere co-occurrence of speaker/company in the same paragraph is unsafe.
        attribution = (
            r"(?<!\w)" + company + r"\s+" + role + r"\s+" + person + r"(?!\w)"
            + "|" + r"(?<!\w)" + person + r"\s*,?\s*" + role +
            r"\s+(?:of|at|for)\s+" + company + r"(?!\w)"
        )
        if re.search(attribution, paragraph, re.I):
            return True
    return False


def _quote_quantity(quote: str, proposal: Proposal) -> tuple[str, str, bool, bool] | None:
    """Verify the proposed number against a unit-bearing token in the quote."""
    approx = bool(APPROX.search(quote))
    lower = bool(LOWER_BOUND.search(quote))
    if proposal.unit == "USD":
        money = re.findall(r"\$\s*([\d,]+(?:\.\d+)?)\s*(million|billion|thousand)?\b", quote, re.I)
        values = [
            _safe_value(Decimal(n.replace(",", "")) *
                {"million": Decimal("1000000"), "billion": Decimal("1000000000"),
                 "thousand": Decimal("1000")}.get((mult or "").lower(), Decimal(1)))
            for n, mult in money
        ]
        if _safe_value(proposal.value) not in values:
            return None
        return _safe_value(proposal.value), "USD", approx, lower
    quantity = _parse_quantity(quote, Candidate("_proposal", proposal.organization,
        proposal.metric or "missile_output", proposal.unit, proposal.kind, "9999-12-31"))
    if quantity is None:
        return None
    value, unit, parsed_approx = quantity
    if not _same_normalized_value(value, proposal.value) or _text(unit) != _text(proposal.unit):
        return None
    return value, unit, approx or parsed_approx, lower


def verify_proposal(candidate: Candidate, lead: Evidence, document: PrimaryDocument,
                    proposal: Proposal) -> Evidence:
    """The model proposes; code checks source spans, scope, dates, modality and figures.

    Unknown grammar, missing source attribution, or forged fields fail closed.
    """
    rejected = replace(lead, comparable=False, verification="candidate",
                       explanation="unverified_proposal")
    if not isinstance(proposal, Proposal):
        rejected.explanation = "invalid_proposal_format"
        return rejected
    if not isinstance(proposal.is_estimate_or_forecast, bool):
        rejected.explanation = "invalid_proposal_format"
        return rejected
    fields = (proposal.value, proposal.unit, proposal.kind, proposal.stage,
              proposal.organization, proposal.speaker, proposal.quote, proposal.date_text)
    if any(not isinstance(x, str) for x in fields) or not isinstance(proposal.metric, str):
        rejected.explanation = "invalid_proposal_format"
        return rejected
    if (not document.primary or document.url != lead.url
            or not document.root_source or not document.text):
        rejected.explanation = "invalid_primary_document"
        return rejected
    quote = proposal.quote
    if not quote.strip() or quote not in document.text:
        rejected.explanation = "quote_not_in_document"
        return rejected
    if proposal.is_estimate_or_forecast:
        rejected.explanation = "model_flags_estimate"
        return rejected
    # Match the original paragraph that contains the exact quote.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", document.text) if quote in p]
    if len(paragraphs) != 1:
        rejected.explanation = "ambiguous_quote_context"
        return rejected
    paragraph = paragraphs[0]
    if (NEGATIVE.search(quote) or SPECULATIVE.search(quote) or
            ANALYST_ESTIMATE.search(paragraph) or CAPACITY.search(paragraph)):
        rejected.explanation = "estimate_plan_negation_or_capacity"
        return rejected
    if not _proposal_subject(candidate, proposal, document, paragraph):
        rejected.explanation = "unverified_actor_or_speaker"
        return rejected
    quantity = _quote_quantity(quote, proposal)
    if quantity is None:
        rejected.explanation = "quantity_not_in_quote"
        return rejected
    value, unit, approx, lower = quantity
    if proposal.date_text.strip().casefold() not in quote.casefold():
        rejected.explanation = "date_text_not_in_quote"
        return rejected
    parsed = _proposed_date(proposal.date_text, document)
    if parsed is None:
        rejected.explanation = "unknown_date_or_missing_publication_date"
        return rejected
    when, precision = parsed
    # The model cannot invent the kind: corroborate from explicit language.
    if proposal.kind != candidate.kind:
        rejected.explanation = "wrong_kind"
        return rejected
    if proposal.kind == "annual_total":
        if re.search(r"\bthis year\b", quote, re.I):
            rejected.explanation = "current_year_incomplete"
            return rejected
        if not re.search(r"\b(?:last year|this year|in \d{4}|during \d{4})\b", quote, re.I):
            rejected.explanation = "annual_period_not_explicit"
            return rejected
        if RATE.search(quote):
            rejected.explanation = "annual_total_confused_with_rate"
            return rejected
    elif proposal.kind == "rate":
        if not RATE.search(quote):
            rejected.explanation = "rate_not_explicit"
            return rejected
    elif proposal.kind == "cumulative":
        if not CUMULATIVE.search(quote) or RATE.search(quote):
            rejected.explanation = "cumulative_not_explicit"
            return rejected
    elif proposal.kind == "point_in_time":
        if proposal.metric != "contract_value" or not CONTRACT_ACTION.search(quote) or not re.search(r"\bcontract\b", quote, re.I):
            rejected.explanation = "point_in_time_not_proven"
            return rejected
    else:
        rejected.explanation = "unsupported_kind"
        return rejected

    if proposal.metric != candidate.metric:
        rejected.explanation = "metric_not_proven"
        return rejected
    if candidate.metric in {"missile_output", "essm_output"}:
        if not MISSILE_OBJECT.search(quote) or VEHICLE_OBJECT.search(quote):
            rejected.explanation = "wrong_object"
            return rejected
        if candidate.metric == "essm_output" and not re.search(
                r"\\bESSM\\b", document.title + " " + paragraph, re.I):
            rejected.explanation = "essm_program_not_established"
            return rejected
        if not OUTPUT_ACTION.search(quote):
            rejected.explanation = "missing_output_action"
            return rejected
    elif candidate.metric == "contract_value":
        if unit != "USD":
            rejected.explanation = "contract_not_currency"
            return rejected
    elif candidate.metric in {"units", "unit_output"}:
        if not re.search(r"\bunits\b", quote, re.I):
            rejected.explanation = "unit_not_explicit"
            return rejected
    else:
        rejected.explanation = "unsupported_metric"
        return rejected
    if candidate.stage and proposal.stage != candidate.stage:
        rejected.explanation = "wrong_stage"
        return rejected
    if proposal.stage == "delivered" and not re.search(r"\b(?:delivered|shipped|supplied)\b", quote, re.I):
        rejected.explanation = "stage_not_in_quote"
        return rejected
    if proposal.stage == "contracted" and not CONTRACT_ACTION.search(quote):
        rejected.explanation = "stage_not_in_quote"
        return rejected
    result = replace(
        lead, value=value, unit=unit, kind=proposal.kind, metric=proposal.metric,
        stage=proposal.stage, subject=candidate.entity, quote=quote,
        value_date=when, date_precision=precision, approximate=approx,
        lower_bound=lower, first_seen_at=document.first_seen_at,
        root_source=document.root_source, scope=document.scope, verification="verified")
    ok, why = _compatible(candidate, result)
    if not ok:
        rejected.explanation = why
        return rejected
    result.comparable = True
    result.explanation = "verified_proposal_against_primary_text"
    return result


def _same_metric(a: str, b: str) -> bool:
    return bool(a.strip() and b.strip()) and _text(a) == _text(b)

def _compatible(candidate: Candidate, item: Evidence) -> tuple[bool, str]:
    if not _same_metric(candidate.metric, item.metric):
        return False, "different_metric"
    if _text(candidate.unit) != _text(item.unit):
        return False, "different_unit_or_currency"
    if candidate.kind != item.kind:
        return False, "rate_vs_cumulative_or_other_kind"
    if candidate.stage and item.stage != candidate.stage:
        return False, "different_or_unknown_stage"
    if item.subject is None or not _identity(candidate, item.subject):
        return False, "unverified_measurement_subject"
    if not item.scope or not item.scope.strip():
        return False, "unknown_measurement_scope"
    if not item.first_seen_at:
        return False, "unknown_first_seen_at"
    try:
        if _date(item.first_seen_at) > _date(candidate.as_of):
            return False, "source_not_available_at_cutoff"
    except ValueError:
        return False, "invalid_first_seen_at"
    if not item.value_date:
        return False, "unknown_value_date"
    try:
        if _date_upper_bound(item.value_date, item.date_precision) > _date(candidate.as_of):
            return False, "after_cutoff"
    except ValueError:
        return False, "invalid_date"
    if not item.url or not item.url.startswith("https://") or not item.quote:
        return False, "missing_quote_or_https_url"
    return True, "same_measure_and_stage_before_cutoff"

def _dedupe(items: list[Evidence]) -> list[Evidence]:
    """Repeated publications of one upstream statement count as one root."""
    chosen: dict[str, Evidence] = {}
    for item in items:
        root = item.root_source or item.url or f"{item.provider}:{item.value}:{item.value_date}"
        if root not in chosen or (item.verification == "verified" and chosen[root].verification != "verified"):
            chosen[root] = item
    return list(chosen.values())

# SQL is read-only by construction and must be run with a READ-ONLY role/session.
# The four column names come from the owner's 2026-11-04 manual trial.
LOCAL_SQL = """
SELECT what, occurred_on, source_url, status
FROM knowledge.edge
WHERE occurred_on <= %(cutoff)s
  AND (what ILIKE ANY(%(names)s) OR what ILIKE ANY(%(metric_words)s))
ORDER BY CASE WHEN what ILIKE ANY(%(names)s) THEN 0 ELSE 1 END,
         occurred_on DESC
LIMIT 20
""".strip()

def local_search(candidate: Candidate, sql_read: Callable[[str, Mapping[str, Any]], Sequence[Mapping[str, Any]]]) -> list[Evidence]:
    def escape(term: str) -> str:
        return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = sql_read(LOCAL_SQL, {
        "cutoff": candidate.as_of,
        "names": [escape(name) for name in _names(candidate)],
        "metric_words": [escape(word) for word in _metric_terms(candidate.metric)],
    })
    if len(rows) > MAX_DB_RESULTS:
        raise ValueError("local reader exceeded result cap")
    evidence = []
    for row in rows:
        # knowledge.edge has no confirmed numerical-value or unit column.
        # Do not hallucinate one from free-text 'what'. These are retrieval leads.
        when = row.get("occurred_on")
        when = when.isoformat()[:10] if hasattr(when, "isoformat") else str(when or "")
        evidence.append(Evidence("knowledge.edge", None, when, str(row.get("what") or "")[:350],
                                 str(row.get("source_url") or "") or None,
                                 candidate.metric, candidate.unit, candidate.kind,
                                 "", False,
                                 "textual_lead_only_no_structured_value",
                                 str(row.get("source_url") or "") or None))
    return evidence

def usaspending_request(candidate: Candidate, endpoint: str) -> dict[str, Any] | None:
    """Return request description ONLY. No network operation.

    USAspending gives spending obligations, *not* manufacturing output or deliveries.
    Explicit user metric must be USD obligations, recipient or subagency entity.
    """
    if candidate.metric != "federal_obligations" or candidate.unit != "USD" or candidate.kind != "annual_total":
        return None
    if endpoint not in ("recipient", "awarding_subagency"):
        raise ValueError("unrecognized category")
    cutoff = _date(candidate.as_of)
    start = date(cutoff.year - 1, 1, 1)
    end = date(cutoff.year - 1, 12, 31)
    if end > cutoff:
        return None
    filters: dict[str, Any] = {"time_period": [{"start_date": start.isoformat(), "end_date": end.isoformat()}]}
    if endpoint == "recipient":
        filters["recipient_search_text"] = list(_names(candidate))
    else:
        # A subagency category needs a verified awarding agency code/filter to be meaningful.
        # Without one do not issue broad searches or claim entity matching.
        return None
    return {"url": USASPENDING_RECIPIENT,
            "body": {"filters": filters, "category": "recipient", "limit": 5,
                     "page": 1, "spending_level": "transactions"},
            "note": "prior calendar-year recipient obligations; verify fiscal-year and recipient identity"}

def usaspending_search(candidate: Candidate, post_json: Callable[[str, Mapping[str, Any]], Mapping[str, Any]]) -> list[Evidence]:
    spec = usaspending_request(candidate, "recipient")
    if spec is None:
        return []
    response = post_json(spec["url"], spec["body"])
    if not isinstance(response, Mapping) or not isinstance(response.get("results"), list):
        raise ValueError("invalid USAspending response")
    out = []
    for row in response["results"][:5]:
        if not isinstance(row, Mapping):
            continue
        name = str(row.get("name") or "")
        if not _identity(candidate, name):
            continue   # prevent conflating subsidiaries and similar names
        value = _safe_value(row.get("amount"))
        if value is None:
            continue
        year = _date(candidate.as_of).year - 1
        out.append(Evidence("USAspending", value, f"{year}-12-31", None,
                            "https://www.usaspending.gov/search",
                            "federal_obligations", "USD", "annual_total", "",
                            False, "aggregate_not_a_primary_quoted_award;calendar_vs_fiscal_year_check",
                            str(row.get("recipient_id") or name)))
    return out

def web_query(candidate: Candidate) -> list[str]:
    # Search backend operators (including before:) are NOT portable. The
    # historical cutoff is enforced by independently verified first_seen_at.
    metric = " ".join(_metric_terms(candidate.metric))
    return [f'"{name}" {metric} {candidate.unit}' for name in _names(candidate)][:MAX_WEB_REQUESTS]

def web_search(candidate: Candidate, search: Callable[[str], Sequence[Mapping[str, Any]]]) -> list[Evidence]:
    """Provider-agnostic discovery. Snippets are leads, never verified facts."""
    found = []
    for query in web_query(candidate)[:MAX_WEB_REQUESTS]:
        for hit in list(search(query))[:5]:
            url = str(hit.get("url") or "")
            title = str(hit.get("title") or "")
            snippet = str(hit.get("snippet") or "")
            if not url.startswith("https://"):
                continue
            found.append(Evidence("web", None, None, (title + ": " + snippet)[:350],
                                  url, candidate.metric, candidate.unit,
                                  candidate.kind, "", False, "snippet_only_requires_primary_source",
                                  str(hit.get("origin_url") or url)))
    return _dedupe(found)

def discover(candidate: Candidate,
             sql_read: Callable[[str, Mapping[str, Any]], Sequence[Mapping[str, Any]]] | None = None,
             post_json: Callable[[str, Mapping[str, Any]], Mapping[str, Any]] | None = None,
             search: Callable[[str], Sequence[Mapping[str, Any]]] | None = None,
             documents: Mapping[str, PrimaryDocument] | None = None,
             max_verifications: int = MAX_VERIFICATIONS,
             propose: Callable[[Candidate, PrimaryDocument], list[Proposal]] | None = None) -> Discovery:
    """Find baseline and second independent root within bounded primary checks."""
    _date(candidate.as_of)
    if not 0 <= max_verifications <= MAX_VERIFICATIONS:
        raise ValueError("invalid max_verifications")
    result = Discovery(candidate.candidate_id)
    stages = [
        ("knowledge.edge", sql_read is not None, lambda: local_search(candidate, sql_read)),
        ("USAspending", post_json is not None, lambda: usaspending_search(candidate, post_json)),
        ("free_web", search is not None, lambda: web_search(candidate, search)),
    ]
    roots: set[str] = set()
    proposed_urls: set[str] = set()
    for provider, enabled, getter in stages:
        if result.baseline is not None and result.second_source is not None:
            result.searched.append({"provider": provider, "status": "not_needed"})
            continue
        if not enabled:
            result.searched.append({"provider": provider, "status": "not_connected"})
            continue
        if provider == "USAspending" and usaspending_request(candidate, "recipient") is None:
            result.searched.append({"provider": provider, "status": "not_comparable_metric"})
            continue
        try:
            leads = _dedupe(getter())
        except Exception as exc:
            result.searched.append({"provider": provider, "status": "error:" + type(exc).__name__})
            continue
        result.searched.append({"provider": provider, "status": "results:" + str(len(leads))})
        skipped = 0
        for lead in leads:
            document = (documents or {}).get(lead.url or "")
            if result.verification_count >= max_verifications:
                skipped += 1
                result.alternatives.append(replace(lead, comparable=False,
                    verification="candidate", explanation="verification_budget_exhausted"))
                continue
            if document is None:
                result.alternatives.append(replace(lead, comparable=False,
                    verification="candidate", explanation="primary_text_not_supplied"))
                continue
            result.verification_count += 1
            checked = verify_primary(candidate, lead, document)
            if not checked.comparable and propose is not None and document.url not in proposed_urls:
                proposed_urls.add(document.url)
                try:
                    suggestions = propose(candidate, document)
                    if not isinstance(suggestions, list) or len(suggestions) > 10:
                        raise ValueError("invalid proposal collection")
                    for suggestion in suggestions:
                        proposal_result = verify_proposal(candidate, lead, document, suggestion)
                        if proposal_result.comparable:
                            checked = proposal_result
                            break
                        checked = proposal_result
                except Exception:
                    checked = replace(lead, verification="candidate", comparable=False,
                                      explanation="proposal_error_or_invalid_format")
            result.alternatives.append(checked)
            if checked.comparable:
                root = checked.root_source or checked.url
                if root in roots:
                    continue
                roots.add(root)
                if result.baseline is None:
                    result.baseline = checked
                elif result.second_source is None:
                    result.second_source = checked
        if skipped:
            result.searched.append({"provider": provider, "status": f"unverified_budget:{skipped}"})
    result.outcome = ("found" if result.baseline is not None else
                      "needs_verification" if result.alternatives else "not_found")
    return result

def to_json(result: Discovery) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2)

# Deliberately no CLI, network client, DB connector or environment-secret handling.
