#!/usr/bin/env python3
"""Read-only OpenAlex research signal pilot. No API credentials or database writes."""
import argparse
import datetime as dt
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.openalex.org/works"
QUERIES = [
    "counter unmanned aircraft systems",
    "electronic warfare",
    "defence supply chain",
    "critical raw materials",
    "autonomous maritime systems",
    "dual use technology",
]

def normalize(work, query):
    title = work.get("display_name") or work.get("title")
    identifier = work.get("id")
    if not identifier or not title:
        return None
    return {
        "external_id": identifier,
        "doi": work.get("doi"),
        "title": title,
        "publication_date": work.get("publication_date"),
        "source_url": work.get("doi") or identifier,
        "publisher_source": ((work.get("primary_location") or {}).get("source") or {}).get("display_name"),
        "signal_type": "innovation_rd",
        "discovery_query": query,
        "source_system": "openalex",
        "is_verified_relevance": False,
    }

def fetch(query, since, per_page, api_key=None):
    params = {"search": query, "filter": "from_publication_date:" + since,
              "per_page": str(per_page), "select": "id,doi,display_name,publication_date,primary_location"}
    if api_key:
        params["api_key"] = api_key
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "BohundefenceResearchPilot/1.0 (public research metadata)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.load(response)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="output/openalex-research.json")
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--per-page", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.days <= 365 or not 1 <= args.per_page <= 100:
        parser.error("days must be 1..365 and per-page 1..100")
    since = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=args.days)).isoformat()
    rows, errors, seen = [], [], set()
    for query in QUERIES:
        try:
            payload = fetch(query, since, args.per_page, os.getenv("OPENALEX_API_KEY"))
            for work in payload.get("results", []):
                item = normalize(work, query)
                if item and item["external_id"] not in seen:
                    rows.append(item)
                    seen.add(item["external_id"])
        except Exception as exc:
            errors.append({"query": query, "error_type": type(exc).__name__})
        time.sleep(0.5)
    result = {"source": "OpenAlex", "since": since, "queries": len(QUERIES),
              "unique_candidates": len(rows), "errors": errors,
              "note": "Metadata candidates only; keyword matches are not verified defence relevance; no DB writes.",
              "items": rows}
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"unique_candidates": len(rows), "errors": len(errors), "output": str(path)}))
    if errors and not rows:
        raise SystemExit("All OpenAlex queries failed; see artifact")

if __name__ == "__main__":
    main()
