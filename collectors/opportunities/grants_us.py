#!/usr/bin/env python3
"""Read-only search of public Grants.gov opportunities. Writes JSON/CSV artifacts, never DB."""
import argparse
import csv
import json
import time
import urllib.request
from pathlib import Path

API = "https://api.grants.gov/v1/api/search2"
KEYWORDS = ["defense", "defence", "dual use", "SBIR", "STTR", "autonomous", "cybersecurity", "aerospace"]

def query(keyword, rows=50):
    body = json.dumps({"keyword": keyword, "rows": rows, "oppStatuses": "posted|forecasted"}).encode()
    request = urllib.request.Request(API, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": "Bohundefence-Opportunities/0.1"})
    with urllib.request.urlopen(request, timeout=45) as response:
        document = json.load(response)
    if str(document.get("errorcode")) != "0" or not isinstance(document.get("data"), dict):
        raise RuntimeError("Grants.gov search2 returned a non-success response")
    hits = document["data"].get("oppHits", [])
    if not isinstance(hits, list):
        raise RuntimeError("Invalid Grants.gov oppHits response")
    return hits

def collect(keywords, rows):
    found = {}
    errors = {}
    for keyword in keywords:
        try:
            for hit in query(keyword, rows):
                opportunity_id = str(hit.get("id") or "").strip()
                if not opportunity_id:
                    continue
                if opportunity_id not in found:
                    found[opportunity_id] = {
                        "external_id": opportunity_id, "title": hit.get("title", ""),
                        "opportunity_number": hit.get("number", ""),
                        "agency": hit.get("agencyName", ""),
                        "agency_code": hit.get("agencyCode", ""),
                        "status": hit.get("oppStatus", ""),
                        "open_date": hit.get("openDate", ""),
                        "close_date": hit.get("closeDate", ""),
                        "source": "Grants.gov search2",
                        "matched_keywords": [],
                    }
                found[opportunity_id]["matched_keywords"].append(keyword)
        except Exception as exc:
            # Never silently transform partial results into a successful complete run.
            errors[keyword] = type(exc).__name__
        time.sleep(0.4)
    return list(found.values()), errors

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="output/grants-us")
    p.add_argument("--rows", type=int, default=50)
    p.add_argument("--keywords", default=",".join(KEYWORDS))
    args = p.parse_args()
    if not 1 <= args.rows <= 100:
        p.error("--rows must be 1..100")
    keywords = [x.strip() for x in args.keywords.split(",") if x.strip()]
    if not keywords:
        p.error("at least one keyword required")
    entries, errors = collect(keywords, args.rows)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {"source": "Grants.gov", "kind": "grant_opportunity",
              "ingestion_status": "read_only_artifact_not_database",
              "keyword_count": len(keywords), "unique_results": len(entries),
              "errors": errors, "items": entries}
    target.with_suffix(".json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with target.with_suffix(".csv").open("w", newline="", encoding="utf-8") as file:
        fields = ["external_id", "title", "opportunity_number", "agency", "agency_code",
                  "status", "open_date", "close_date", "source", "matched_keywords"]
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for item in entries:
            writer.writerow({**item, "matched_keywords": "|".join(item["matched_keywords"])})
    print(f"GRANTS keywords={len(keywords)} unique={len(entries)} errors={len(errors)}")
    if errors or not entries:
        raise RuntimeError("Incomplete or empty grants collection; inspect artifact")

if __name__ == "__main__":
    main()
