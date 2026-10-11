#!/usr/bin/env python3
"""Read-only EU Funding & Tenders Search API smoke probe; no persistence."""
import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid

API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
EDF_PROGRAMME = "44181033"  # EDF; cross-check against EC portal filters

def query_data():
    return {"bool": {"must": [
        {"terms": {"type": ["1", "8"]}},  # grants and cascade funding
        {"terms": {"status": ["31094501", "31094502"]}},  # forthcoming and open
        {"term": {"frameworkProgramme": EDF_PROGRAMME}},
    ]}}

def multipart(fields):
    boundary = "----bohun" + uuid.uuid4().hex
    chunks = []
    for name, value in fields.items():
        chunks.extend([
            ("--" + boundary + "\r\n").encode(),
            (f'Content-Disposition: form-data; name="{name}"\r\n').encode(),
            b"Content-Type: application/json\r\n\r\n",
            json.dumps(value, separators=(",", ":")).encode(),
            b"\r\n",
        ])
    chunks.append(("--" + boundary + "--\r\n").encode())
    return b"".join(chunks), "multipart/form-data; boundary=" + boundary

def nested_shape(hit):
    """Only metadata key names and value types; no raw nested content or URLs."""
    out = {}
    for field in ("metadata", "enrichedMetadata", "reference", "summary", "content"):
        value = hit.get(field)
        if isinstance(value, dict):
            out[field] = {"type": "dict", "keys": list(value.keys())[:35],
                          "value_types": {str(k): type(v).__name__ for k, v in list(value.items())[:35]}}
        elif isinstance(value, list):
            out[field] = {"type": "list", "length": len(value),
                          "first_type": type(value[0]).__name__ if value else None,
                          "first_keys": list(value[0].keys())[:25] if value and isinstance(value[0], dict) else []}
        else:
            out[field] = {"type": type(value).__name__}
    return out

def summarize_response(data):
    """Schema discovery only. Do not treat returned search hits as approved grants."""
    if not isinstance(data, dict):
        raise ValueError("Unexpected API response: root is not an object")
    hits = data.get("results")
    if not isinstance(hits, list):
        raise ValueError("Unexpected API response: results is not a list")
    preview = []
    for hit in hits[:3]:
        if not isinstance(hit, dict):
            continue
        # Keys are discovered rather than assumed; field names may change.
        # Include only a bounded sample of scalar values, never full raw records.
        scalar = {k: str(v)[:180] for k, v in hit.items()
                  if isinstance(v, (str, int, float, bool)) and
                  any(token in k.lower() for token in
                      ("title", "identifier", "id", "deadline", "date", "status", "type"))}
        preview.append({"keys": list(hit.keys())[:35], "scalar_candidates": scalar, "nested_shape": nested_shape(hit)})
    return {"total_results": data.get("totalResults"),
            "returned_count": len(hits),
            "warnings_count": len(data.get("warnings") or []),
            "sample_hits": preview}

def smoke(page_size, timeout):
    params = urllib.parse.urlencode({
        "apiKey": "SEDIA", "text": "*", "pageSize": page_size, "pageNumber": 1
    })
    body, content_type = multipart({"query": query_data(), "languages": ["en"]})
    req = urllib.request.Request(
        API + "?" + params, data=body, method="POST",
        headers={"Content-Type": content_type, "Accept": "application/json",
                 "User-Agent": "BOHUN-research-readonly/0.2",
                 "Referer": "https://ec.europa.eu/info/funding-tenders/opportunities/portal/"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Response exceeds 2 MB diagnostic cap")
        data = json.loads(raw)
        return {"http_status": response.status, "root_type": type(data).__name__,
                "root_keys": list(data)[:30] if isinstance(data, dict) else [],
                "root_field_types": {k: type(v).__name__ for k, v in data.items()}
                if isinstance(data, dict) else {},
                **summarize_response(data),
                "persisted": 0, "publication_approved": 0}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=3)
    p.add_argument("--timeout", type=int, default=20)
    args = p.parse_args()
    if not 1 <= args.limit <= 10:
        p.error("--limit must be between 1 and 10")
    try:
        output = smoke(args.limit, args.timeout)
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "message": str(exc)[:300], "publication_approved": 0}))
        raise SystemExit(1)
    print(json.dumps({"ok": True, **output}, indent=2))

if __name__ == "__main__":
    main()
