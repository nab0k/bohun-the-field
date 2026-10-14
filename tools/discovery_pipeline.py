#!/usr/bin/env python3
"""Offline, resumable bridge: directory candidate CSV -> normalized domains -> RSS seed list.

Does not claim source verification or subscribe to Inoreader.
"""
import argparse
import csv
import json
from pathlib import Path
from urllib.parse import urlsplit

def normalize(url):
    p = urlsplit(url.strip())
    if p.scheme not in ("https", "http") or not p.hostname or p.username or p.password:
        return None
    name = p.hostname.lower().removeprefix("www.")
    if name in ("localhost",) or name.endswith((".local", ".internal", ".test")):
        return None
    return name, f"https://{name}/"

def read_rows(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--inputs", nargs="+", required=True, help="CSV from v2/v3")
    p.add_argument("--state", default="output/discovery-pipeline/registry.csv")
    p.add_argument("--output", default="output/discovery-pipeline")
    a = p.parse_args()
    out = Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    registry = Path(a.state)
    known = {}
    for row in read_rows(registry):
        known[row["domain"]] = row
    before = set(known)
    input_count = 0
    for source in a.inputs:
        for row in read_rows(Path(source)):
            input_count += 1
            result = normalize(row.get("website", ""))
            if not result:
                continue
            domain, site = result
            if domain in known:
                continue
            known[domain] = {"domain": domain, "website": site,
                             "name_hint": row.get("name") or row.get("name_hint") or "",
                             "source_page": row.get("source_page") or row.get("directory_page") or row.get("directory_url") or "",
                             "review_status": "unverified_candidate"}
    fields = ["domain", "website", "name_hint", "source_page", "review_status"]
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows({k: v.get(k, "") for k in fields} for _, v in sorted(known.items()))
    new = sorted(set(known) - before)
    with (out / "new-sites.txt").open("w", encoding="utf-8") as f:
        for domain in new:
            f.write(known[domain]["website"] + "\n")
    report = {"input_rows": input_count, "registry_total": len(known),
              "new_candidate_domains": len(new), "feed_validation_done": False,
              "production_writes": 0, "inoreader_subscriptions_added": 0}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))

if __name__ == "__main__":
    main()
