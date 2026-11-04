"""C4-08 baseline discovery, opt-in adapters only.

No database connection, API request, or search service is created here.
The caller supplies a read-only SQL runner and optional HTTP/search transports.
Outputs evidence candidates, never signal decisions or publication permissions.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Sequence
import json

USASPENDING_RECIPIENT = "https://api.usaspending.gov/api/v2/search/spending_by_category/recipient/"
USASPENDING_SUBAGENCY = "https://api.usaspending.gov/api/v2/search/spending_by_category/awarding_subagency/"
MAX_DB_RESULTS = 20
MAX_API_REQUESTS = 2
MAX_WEB_REQUESTS = 2

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

@dataclass
class Discovery:
    candidate_id: str
    baseline: Evidence | None = None
    alternatives: list[Evidence] = field(default_factory=list)
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
    if item.subject is None or _text(item.subject) != _text(candidate.entity):
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
  AND (what ILIKE %(entity)s ESCAPE '\\' OR what ILIKE %(metric)s ESCAPE '\\')
ORDER BY occurred_on DESC
LIMIT 20
""".strip()

def local_search(candidate: Candidate, sql_read: Callable[[str, Mapping[str, Any]], Sequence[Mapping[str, Any]]]) -> list[Evidence]:
    rows = sql_read(LOCAL_SQL, {
        "cutoff": candidate.as_of,
        "entity": "%" + candidate.entity.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
        "metric": "%" + candidate.metric.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%",
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
                                 str(row.get("status") or ""), False,
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
        filters["recipient_search_text"] = [candidate.entity]
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
        if _text(name) != _text(candidate.entity):
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
    words = [candidate.entity, candidate.metric.replace("_", " "), candidate.unit,
             f"before:{candidate.as_of}"]
    return [" ".join(words), " ".join([candidate.entity, candidate.metric.replace("_", " "),
                                       "official report before:" + candidate.as_of])]

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
             verify: Callable[[Candidate, Evidence], Evidence] | None = None) -> Discovery:
    """Search strictly in order. Every adapter is optional and injected by caller.

    verify() MUST independently check a primary document, date, quote, number,
    subject, scope, first_seen_at and provenance before marking 'verified'.
    No default or LLM self-approval exists. This is a contract, not attestation
    that a caller's verifier is trustworthy.
    """
    _date(candidate.as_of)
    output = Discovery(candidate.candidate_id)
    stages = [
        ("knowledge.edge", sql_read is not None,
         lambda: local_search(candidate, sql_read)),
        ("USAspending", post_json is not None,
         lambda: usaspending_search(candidate, post_json)),
        ("free_web", search is not None,
         lambda: web_search(candidate, search)),
    ]
    for label, enabled, get_results in stages:
        if not enabled:
            output.searched.append({"provider": label, "status": "not_connected"})
            continue
        if label == "USAspending" and usaspending_request(candidate, "recipient") is None:
            output.searched.append({"provider": label, "status": "not_comparable_metric"})
            continue
        try:
            raw = get_results()
        except Exception as ex:
            # Failure must be visible; later provider may still provide evidence.
            output.searched.append({"provider": label, "status": "error:" + type(ex).__name__})
            continue
        output.searched.append({"provider": label, "status": "results:" + str(len(raw))})
        for lead in _dedupe(raw):
            if verify is not None:
                try:
                    checked = verify(candidate, lead)
                    if not isinstance(checked, Evidence):
                        raise ValueError("verifier must return Evidence")
                    lead = checked
                except Exception:
                    lead.verification = "candidate"
                    lead.explanation = "verification_failed"
            comparable, why = _compatible(candidate, lead)
            lead.comparable = comparable and lead.verification == "verified" and bool(_safe_value(lead.value))
            lead.explanation = why if not comparable else (
                "verified_comparable" if lead.comparable else "requires_independent_verification")
            output.alternatives.append(lead)
            if lead.comparable:
                output.baseline = lead
                output.outcome = "found"
                return output
    output.outcome = "needs_verification" if output.alternatives else "not_found"
    return output

def to_json(result: Discovery) -> str:
    return json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2)

# Deliberately no CLI, network client, DB connector or environment-secret handling.
