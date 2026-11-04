"""C4-08 baseline discovery, opt-in adapters only.

No database connection, API request, or search service is created here.
The caller supplies a read-only SQL runner and optional HTTP/search transports.
Outputs evidence candidates, never signal decisions or publication permissions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import date
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

@dataclass(frozen=True)
class PrimaryDocument:
    url: str
    text: str
    first_seen_at: str
    root_source: str
    scope: str
    primary: bool = True

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
        return str(n) if n.is_finite() else None
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

def verify_primary(candidate: Candidate, lead: Evidence, document: PrimaryDocument) -> Evidence:
    """Check literal number, unit, ISO date, exact quote in caller-supplied primary text.

    The caller is responsible for authenticity and first_seen_at provenance.
    """
    failed = replace(lead, verification="candidate", comparable=False)
    if not document.primary or document.url != lead.url or not document.url.startswith("https://"):
        failed.explanation = "primary_document_mismatch"
        return failed
    quote = (lead.quote or "").strip()
    # For text-only local leads and web snippets, find a literal complete line
    # in the caller-supplied primary document. Never trust a snippet as a quote.
    if not quote or quote not in document.text:
        quote = next((line.strip() for line in document.text.splitlines()
                      if len(line.strip()) >= 12
                      and any(name.casefold() in line.casefold() for name in _names(candidate))
                      and lead.unit.casefold() in line.casefold()), "")
    if not quote and lead.quote:
        quote = lead.quote.strip()
    if len(quote) < 12 or quote not in document.text:
        failed.explanation = "quote_not_found_in_primary_text"
        return failed
    value = lead.value
    if value is None:
        match = re.search(r"(?<![\\w.])([-+]?\\d[\\d,]*(?:\\.\\d+)?)\\s*" +
                          re.escape(lead.unit) + r"(?![\\w/])", quote, flags=re.I)
        value = match.group(1).replace(",", "") if match else None
    if not value or not _number_in(value, quote):
        failed.explanation = "number_not_in_quote"
        return failed
    if not lead.unit or lead.unit.casefold() not in quote.casefold():
        failed.explanation = "unit_not_in_quote"
        return failed
    value_date = lead.value_date
    if value_date is None:
        dates = re.findall(r"\\b\\d{4}-\\d{2}-\\d{2}\\b", quote)
        value_date = dates[0] if len(dates) == 1 else None
    if not value_date or value_date not in quote:
        failed.explanation = "date_not_in_quote"
        return failed
    if not any(name.casefold() in quote.casefold() for name in _names(candidate)):
        failed.explanation = "subject_not_in_quote"
        return failed
    if candidate.stage and candidate.stage.casefold() not in quote.casefold():
        failed.explanation = "stage_not_in_quote"
        return failed
    checked = replace(lead, verification="verified", first_seen_at=document.first_seen_at,
                      root_source=document.root_source or document.url, subject=candidate.entity,
                      quote=quote, value=value, value_date=value_date, scope=document.scope)
    valid, reason = _compatible(candidate, checked)
    if not valid:
        failed.explanation = reason
        return failed
    checked.comparable = True
    checked.explanation = "verified_primary_quote_number_unit_date"
    return checked


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
        if _date(item.value_date) > _date(candidate.as_of):
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
ORDER BY occurred_on DESC
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
             max_verifications: int = MAX_VERIFICATIONS) -> Discovery:
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
