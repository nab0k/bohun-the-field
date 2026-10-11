#!/usr/bin/env python3
"""Read-only diagnostic for the European Commission Funding & Tenders search API.

No credentials, database writes or publication. HTTP errors are reported, not hidden.
"""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
DEFAULT_QUERY = "European Defence Fund"

def request_payload(query, page_size):
    # The EC Search API expects a POST JSON request with a query and pagination.
    return {"query": {"bool": {"must": [{"query_string": {"query": query}}]}},
            "from": 0, "size": page_size}

def smoke(query, page_size, timeout):
    url = API + "?" + urllib.parse.urlencode({"apiKey": "SEDIA", "text": query})
    payload = json.dumps(request_payload(query, page_size)).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json",
                                          "User-Agent": "BOHUN-readonly-collector-diagnostic/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("API response exceeds diagnostic 2MB cap")
        parsed = json.loads(raw)
        return {"http_status": response.status, "root_type": type(parsed).__name__,
                "root_keys": list(parsed.keys())[:30] if isinstance(parsed, dict) else [],
                "sample_structure": {k: type(v).__name__ for k, v in parsed.items()} if isinstance(parsed, dict) else {},
                "raw_records_persisted": 0, "publication_approved": 0}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--query", default=DEFAULT_QUERY)
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--timeout", type=int, default=20)
    args = p.parse_args()
    if not 1 <= args.limit <= 10:
        p.error("--limit must be between 1 and 10")
    try:
        result = smoke(args.query, args.limit, args.timeout)
    except (urllib.error.URLError, ValueError, TimeoutError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "message": str(exc)[:300], "publication_approved": 0}))
        raise SystemExit(1)
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
