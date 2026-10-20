"""UK Find a Tender, CanadaBuys and EU SEDIA: bounded, diagnostic-only readers.

No DB, publishing, secrets, paid API or AI. No network on import.
Raw public-source payloads are held in memory, never written to disk.
Live response structures require a separate real-data verification step.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

UK = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
CANADA = {
    "ca_new": "https://canadabuys.canada.ca/opendata/pub/newTenderNotice-nouvelAvisAppelOffres.csv",
    "ca_open": "https://canadabuys.canada.ca/opendata/pub/openTenderNotice-ouvertAvisAppelOffres.csv",
}
EU = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
LIMIT_BYTES = 16_000_000
USER_AGENT = "BohunReviewSourceProbe/0.1 (metadata-only)"


class ProbeError(ValueError):
    pass


def _get(url, *, body=None, content_type=None, timeout=20):
    """Allowlisted HTTPS; no redirects; bounded bytes; stop on rate limits."""
    allowed = {
        "www.find-tender.service.gov.uk": "/api/1.0/ocdsReleasePackages",
        "canadabuys.canada.ca": "/opendata/pub/",
        "api.tech.ec.europa.eu": "/search-api/prod/rest/search",
    }
    p = urllib.parse.urlsplit(url)
    if (p.scheme != "https" or p.netloc not in allowed or
            not p.path.startswith(allowed[p.netloc]) or
            p.username or p.password or p.fragment):
        raise ProbeError("unapproved_endpoint")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise ProbeError("redirect_blocked")

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/csv"}
    if content_type:
        headers["Content-Type"] = content_type
    request = urllib.request.Request(
        url, data=body, method="POST" if body is not None else "GET", headers=headers
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            payload = response.read(LIMIT_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ProbeError(f"http_{exc.code}") from None
    except urllib.error.URLError:
        raise ProbeError("network_unavailable") from None
    if len(payload) > LIMIT_BYTES:
        raise ProbeError("response_size_limit")
    return payload


def _json(raw):
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ProbeError("invalid_json") from None
    if not isinstance(result, dict):
        raise ProbeError("json_object_expected")
    return result


def _receipt(raw, records):
    return {
        "response_bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "records_on_page": len(records),
        "record_keys": sorted({
            k for x in records[:3] if isinstance(x, dict) for k in x
        })[:40],
    }


def uk_probe(start, end, *, max_pages=2, transport=_get):
    """One-hour UK update window; reject unsafe next links and partial coverage."""
    try:
        a, b = [
            datetime.strptime(v, "%Y-%m-%dT%H:%M:%S") for v in (start, end)
        ]
    except ValueError:
        raise ProbeError("bad_uk_window") from None
    if a >= b or b - a > timedelta(hours=1) or not 1 <= max_pages <= 2:
        raise ProbeError("uk_window_or_page_limit")

    cursor, receipts = None, []
    for page_no in range(1, max_pages + 1):
        params = {"updatedFrom": start, "updatedTo": end, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        raw = transport(UK + "?" + urllib.parse.urlencode(params))
        result = _json(raw)
        releases = result.get("releases")
        if not isinstance(releases, list):
            raise ProbeError("uk_missing_releases")
        receipts.append(_receipt(raw, releases))
        links = result.get("links") or {}
        if not isinstance(links, dict):
            raise ProbeError("uk_bad_links")
        nxt = links.get("next")
        if not nxt:
            return {
                "source": "uk_fts", "complete": True, "pages": page_no,
                "records": sum(v["records_on_page"] for v in receipts),
                "receipts": receipts,
            }
        if not isinstance(nxt, str) or len(nxt) > 2048:
            raise ProbeError("uk_bad_next")
        u = urllib.parse.urlsplit(urllib.parse.urljoin(UK, nxt))
        if (u.scheme != "https" or u.netloc != "www.find-tender.service.gov.uk"
                or u.path != "/api/1.0/ocdsReleasePackages"):
            raise ProbeError("uk_next_other_endpoint")
        query = urllib.parse.parse_qs(u.query, keep_blank_values=True)
        if set(query) - {"cursor", "updatedFrom", "updatedTo", "limit"}:
            raise ProbeError("uk_next_unknown_params")
        if (query.get("updatedFrom", [start]) != [start] or
                query.get("updatedTo", [end]) != [end] or
                query.get("limit", ["100"]) != ["100"]):
            raise ProbeError("uk_next_changed_scope")
        new_cursor = query.get("cursor")
        if (not new_cursor or len(new_cursor) != 1 or
                not re.fullmatch(r"[A-Za-z0-9=]{1,300}", new_cursor[0]) or
                new_cursor[0] == cursor):
            raise ProbeError("uk_next_bad_cursor")
        cursor = new_cursor[0]
    raise ProbeError("uk_incomplete_bounded_window")


def canada_probe(source, *, transport=_get):
    if source not in CANADA:
        raise ProbeError("canada_unknown_scope")
    raw = transport(CANADA[source])
    try:
        data = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ProbeError("canada_encoding") from None
    reader = csv.DictReader(io.StringIO(data))
    fields = reader.fieldnames
    if not fields or len(fields) != len(set(fields)):
        raise ProbeError("canada_bad_csv_header")
    count = 0
    for row in reader:
        if None in row:
            raise ProbeError("canada_ragged_row")
        count += 1
        if count > 50_000:
            raise ProbeError("canada_record_limit")
    return {
        "source": source, "complete": True, "records": count,
        "header_fields": fields[:80], "receipts": [_receipt(raw, [])],
        "note": "Federal data only; omission never implies cancellation",
    }


def _eu_body():
    """Public SEDIA search index; no secret API key or authentication."""
    query = {"bool": {"must": [
        {"terms": {"type": ["1", "2", "8"]}},
        {"terms": {"status": ["31094501", "31094502", "31094503"]}},
    ]}}
    boundary = "BOHUNPROBEBOUNDARY"
    fields = [
        ("query", "application/json", json.dumps(query, separators=(",", ":"))),
        ("languages", "application/json", '["en"]'),
        ("displayLanguage", "text/plain", "en"),
    ]
    chunks = [
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n'
         f'Content-Type: {ctype}\r\n\r\n{value}\r\n').encode()
        for name, ctype, value in fields
    ]
    return (
        b"".join(chunks) + f"--{boundary}--\r\n".encode(),
        f"multipart/form-data; boundary={boundary}",
    )


def eu_probe(term="EDF", *, transport=_get):
    if not term or len(term) > 60 or any(c in term for c in "\r\n"):
        raise ProbeError("eu_bad_search_term")
    url = EU + "?" + urllib.parse.urlencode({
        "apiKey": "SEDIA", "text": term, "pageNumber": 1, "pageSize": 50
    })
    body, content_type = _eu_body()
    raw = transport(url, body=body, content_type=content_type)
    result = _json(raw)
    records = result.get("results")
    if not isinstance(records, list):
        raise ProbeError("eu_unknown_wire_format")
    return {
        "source": "eu_funding_grants", "complete": False, "pages": 1,
        "records": len(records), "reported_total": result.get("totalResults"),
        "receipts": [_receipt(raw, records)],
        "note": "Single-page diagnostic, not exhaustive EDF monitoring",
    }


def main():
    parser = argparse.ArgumentParser(description="Manual read-only UK/CA/EU review")
    parser.add_argument("--live-read-only", action="store_true", required=True)
    parser.add_argument("--source", required=True,
                        choices=["uk", "ca_new", "ca_open", "eu"])
    parser.add_argument("--uk-from", default="2026-10-15T09:00:00")
    parser.add_argument("--uk-to", default="2026-10-15T10:00:00")
    args = parser.parse_args()
    try:
        if args.source == "uk":
            result = uk_probe(args.uk_from, args.uk_to)
        elif args.source in CANADA:
            result = canada_probe(args.source)
        else:
            result = eu_probe("EDF")
        print(json.dumps({"mode": "READ_ONLY", "qa": "HOLD",
                          "public_release": False, **result}))
    except ProbeError as exc:
        print(json.dumps({"mode": "READ_ONLY", "status": "STOPPED",
                          "source": args.source, "reason": str(exc)}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
