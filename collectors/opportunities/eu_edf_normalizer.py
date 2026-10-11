#!/usr/bin/env python3
"""Normalize EU Funding & Tenders search hits in memory. Never persist or publish."""
from datetime import datetime, timezone
from urllib.parse import urlparse

FIELDS = ("title", "identifier", "callIdentifier", "deadlineDate",
          "startDate", "frameworkProgramme", "type", "sortStatus", "url")

def first(metadata, key):
    values = metadata.get(key)
    if not isinstance(values, list):
        return None
    for value in values:
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()[:500]
    return None

def official_url(value):
    if not value:
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https":
        return None
    if parsed.hostname not in ("ec.europa.eu", "api.tech.ec.europa.eu",
                               "funding-tenders.ec.europa.eu"):
        return None
    return value[:1000]

def date_value(value):
    if not value:
        return None
    try:
        # API may return ISO date or datetime. Preserve the original for review.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.date().isoformat()
    except ValueError:
        return None

def normalize(hit):
    metadata = hit.get("metadata") if isinstance(hit, dict) else None
    if not isinstance(metadata, dict):
        metadata = {}
    fields = {field: first(metadata, field) for field in FIELDS}
    issues = []
    if not fields["identifier"]:
        issues.append("missing_identifier")
    if not fields["title"]:
        issues.append("missing_title")
    if not fields["deadlineDate"]:
        issues.append("missing_deadline")
    elif not date_value(fields["deadlineDate"]):
        issues.append("invalid_deadline")
    url = official_url(fields["url"])
    if not url:
        issues.append("missing_or_nonofficial_url")
    if fields["frameworkProgramme"] != "44181033":
        issues.append("unverified_programme")
    return {
        "source": "EU_FUNDING_TENDERS_EDF",
        "identifier": fields["identifier"],
        "call_identifier": fields["callIdentifier"],
        "title": fields["title"],
        "deadline_raw": fields["deadlineDate"],
        "deadline_date": date_value(fields["deadlineDate"]),
        "start_date": date_value(fields["startDate"]),
        "programme": fields["frameworkProgramme"],
        "opportunity_type": fields["type"],
        "status_raw": fields["sortStatus"],
        "official_url": url,
        "qa_issues": issues,
        "qa_status": "HOLD",
        "publication_approved": False,
    }

def normalize_page(response):
    hits = response.get("results")
    if not isinstance(hits, list):
        raise ValueError("results must be a list")
    records = [normalize(hit) for hit in hits]
    identifiers = [r["identifier"] for r in records if r["identifier"]]
    return {"source_total_results": response.get("totalResults"),
            "page_records": len(records),
            "unique_identifiers_on_page": len(set(identifiers)),
            "records": records,
            "persisted": 0,
            "publication_approved": 0}
