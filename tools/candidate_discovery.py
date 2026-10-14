#!/usr/bin/env python3
"""Discover organisation website candidates from public industry directory pages.

Outputs candidates for review; no subscriptions or production writes.
Directory pages must be curated, and their HTML structure reviewed periodically.
"""
import argparse
import csv
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup

UA = "BOHUN-Candidate-Discovery/1.0 (public research)"
BLOCK = ("facebook.com", "instagram.com", "linkedin.com", "youtube.com",
         "twitter.com", "x.com", "tiktok.com", "google.com", "wikipedia.org",
         "cloudflare.com", "cookiebot.com", "github.com", "mailto:")
SKIP = ("/privacy", "/terms", "/cookie", "/login", "/contact", "/about",
        "/faq", "/events", "/news", "/blog", "/press", "/media", "/search")
KEYWORDS = ("defence", "defense", "military", "aerospace", "drone", "unmanned",
            "dual-use", "cyber", "radar", "naval", "space", "security",
            "supplier", "member", "exhibitor", "portfolio", "company",
            "startup", "innovation", "partner", "technology", "manufacturer",
            "обор", "військ", "оборон", "défense", "verteidig", "zbrojen")
MAX_BYTES = 1_500_000

def clean(url):
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        return ""
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", "", ""))

def domain(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")

def get(session, url):
    try:
        response = session.get(url, timeout=(4, 10), stream=True)
        if response.status_code != 200 or int(response.headers.get("Content-Length", 0)) > MAX_BYTES:
            response.close()
            return None
        data = bytearray()
        for part in response.iter_content(32768):
            data.extend(part)
            if len(data) > MAX_BYTES:
                response.close()
                return None
        response._content = bytes(data)
        return response
    except (requests.RequestException, ValueError):
        return None

def robots_ok(session, url, cache):
    p = urlsplit(url)
    base = p.scheme + "://" + p.netloc
    if base not in cache:
        response = get(session, base + "/robots.txt")
        if response is None:
            cache[base] = None
        else:
            parser = RobotFileParser()
            parser.parse(response.text.splitlines())
            cache[base] = parser
    return cache[base] is not None and cache[base].can_fetch(UA, url)

def collect(page, category, session, robots):
    if not robots_ok(session, page, robots):
        return [], "robots_unavailable_or_disallowed"
    response = get(session, page)
    if response is None:
        return [], "unavailable"
    soup = BeautifulSoup(response.content, "html.parser")
    source_host = domain(response.url)
    records = []
    for a in soup.select("a[href]")[:4000]:
        url = clean(urljoin(response.url, a["href"]))
        if not url or domain(url) == source_host:
            continue
        if any(x in domain(url) for x in BLOCK):
            continue
        label = a.get_text(" ", strip=True)[:180]
        context = a.parent.get_text(" ", strip=True)[:350] if a.parent else label
        score = sum(1 for word in KEYWORDS if word in (label + " " + context + " " + url).lower())
        if score == 0:
            continue
        if any(x in urlsplit(url).path.lower() for x in SKIP):
            continue
        records.append({"domain": domain(url), "website": url, "name_hint": label,
                        "directory_url": page, "directory_type": category,
                        "evidence": context, "keyword_score": score,
                        "review_status": "unverified_candidate"})
    return records, "scanned"

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="data/sources/discovery-directories.csv")
    p.add_argument("--output", default="output/candidate-discovery")
    p.add_argument("--max-pages", type=int, default=100)
    p.add_argument("--delay", type=float, default=1.0)
    args = p.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = UA
    robots = {}
    with open(args.catalog, newline="", encoding="utf-8") as fh:
        pages = list(csv.DictReader(fh))[:args.max_pages]
    unique = {}
    audits = []
    for item in pages:
        page = item["directory_url"].strip()
        category = item["directory_type"].strip()
        try:
            results, status = collect(page, category, session, robots)
        except Exception as exc:
            results, status = [], "error:" + type(exc).__name__
        for record in results:
            key = record["domain"]
            if key not in unique or record["keyword_score"] > unique[key]["keyword_score"]:
                unique[key] = record
        audits.append({"directory_url": page, "directory_type": category,
                       "status": status, "candidate_links": len(results)})
        print(json.dumps(audits[-1]), flush=True)
        time.sleep(max(0, args.delay))
    fields = ["domain", "website", "name_hint", "directory_url", "directory_type",
              "evidence", "keyword_score", "review_status"]
    with (out / "candidates.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(sorted(unique.values(), key=lambda r: (-r["keyword_score"], r["domain"])))
    with (out / "directory-audit.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["directory_url", "directory_type", "status", "candidate_links"])
        w.writeheader()
        w.writerows(audits)
    report = {"directory_pages_checked": len(audits), "unique_unverified_candidates": len(unique),
              "successful_pages": sum(x["status"] == "scanned" for x in audits),
              "production_writes": 0, "inoreader_subscriptions_added": 0,
              "note": "External links are unverified leads, not confirmed defence companies or RSS."}
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)

if __name__ == "__main__":
    main()
