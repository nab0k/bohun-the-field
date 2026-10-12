#!/usr/bin/env python3
"""Read-only, bounded paginated EDF search audit; prints aggregate QA only."""
import argparse
import json
import urllib.parse
import urllib.request

from collectors.opportunities.eu_funding_api_smoke import API, multipart, query_data
from collectors.opportunities.eu_edf_normalizer import normalize

def fetch_page(page, page_size, timeout):
    params = urllib.parse.urlencode({"apiKey": "SEDIA", "text": "*",
                                     "pageSize": page_size, "pageNumber": page})
    body, content_type = multipart({"query": query_data(), "languages": ["en"]})
    request = urllib.request.Request(
        API + "?" + params, data=body, method="POST",
        headers={"Content-Type": content_type, "Accept": "application/json",
                 "User-Agent": "BOHUN-readonly-audit/0.1",
                 "Referer": "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("API page exceeds 2 MB")
        result = json.loads(raw)
    if not isinstance(result, dict) or not isinstance(result.get("results"), list):
        raise ValueError("Unexpected search API response")
    return result

def audit(pages, page_size, fetcher):
    seen = set()
    qa_counts = {}
    total = None
    processed = 0
    duplicates = 0
    missing_ids = 0
    status_deadline_matrix = {}
    for page in range(1, pages + 1):
        data = fetcher(page, page_size)
        reported = data.get("totalResults")
        if not isinstance(reported, int) or reported < 0:
            raise ValueError("Invalid totalResults")
        if total is None:
            total = reported
        elif reported != total:
            raise ValueError("Search result count changed between pages")
        hits = data["results"]
        if not hits:
            break
        for hit in hits:
            processed += 1
            record = normalize(hit)
            key = record["identifier"]
            if not key:
                missing_ids += 1
            elif key in seen:
                duplicates += 1
            else:
                seen.add(key)
            status = record["status_raw"] or "MISSING"
            deadline_class = ("expired" if "expired_deadline" in record["qa_issues"]
                              else "missing_or_invalid" if any(x in record["qa_issues"] for x in ("missing_deadline", "invalid_deadline"))
                              else "not_expired")
            bucket = status_deadline_matrix.setdefault(status, {"expired": 0, "not_expired": 0, "missing_or_invalid": 0})
            bucket[deadline_class] += 1
            for issue in record["qa_issues"]:
                qa_counts[issue] = qa_counts.get(issue, 0) + 1
        if processed >= total or len(hits) < page_size:
            break
    return {"reported_total": total, "processed": processed,
            "unique_identifiers": len(seen), "duplicate_identifiers": duplicates,
            "missing_identifiers": missing_ids, "qa_issue_counts": qa_counts,
            "status_deadline_matrix": status_deadline_matrix,
            "complete": processed >= total if total is not None else False,
            "persisted": 0, "publication_approved": 0}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=5)
    parser.add_argument("--page-size", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.pages <= 10 or not 1 <= args.page_size <= 20:
        parser.error("pages must be 1..10 and page-size must be 1..20")
    result = audit(args.pages, args.page_size,
                   lambda page, size: fetch_page(page, size, args.timeout))
    print(json.dumps(result, indent=2))
    if not result["complete"]:
        raise SystemExit("Incomplete audit; no data persisted")

if __name__ == "__main__":
    main()
