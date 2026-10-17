#!/usr/bin/env python3
"""Validate candidate sites for RSS/Atom feeds. No subscriptions or production writes."""
import argparse
import csv
import json
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
import feedparser
import requests

HINTS = ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml", "/news/feed")
HEADERS = {"User-Agent": "BOHUN-Feed-Validation/1.0 (public feed research)"}

def check(session, url):
    try:
        with session.get(url, headers=HEADERS, timeout=(4, 8), stream=True) as response:
            if response.status_code != 200:
                return None
            data = bytearray()
            for chunk in response.iter_content(32768):
                data.extend(chunk)
                if len(data) > 750000:
                    return None
            parsed = feedparser.parse(bytes(data))
            if parsed.get("version") and parsed.get("entries") and not parsed.get("bozo", False):
                return {"feed_url": response.url, "entries": len(parsed.entries), "title": parsed.feed.get("title", "")[:200]}
    except requests.RequestException:
        return None
    return None

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sites", required=True)
    p.add_argument("--output", default="output/discovery-pipeline")
    p.add_argument("--max-sites", type=int, default=300)
    args = p.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    sites = [line.strip() for line in Path(args.sites).read_text().splitlines() if line.strip()][:args.max_sites]
    session = requests.Session()
    found = []
    for i, site in enumerate(sites):
        for path in HINTS:
            feed = check(session, urljoin(site, path))
            if feed:
                found.append({"site": site, **feed, "review_status": "unverified_relevance"})
                break
        print(json.dumps({"site_number": i + 1, "sites_total": len(sites), "feeds_found": len(found)}), flush=True)
    with (output / "candidate-feeds.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["site", "feed_url", "entries", "title", "review_status"])
        w.writeheader()
        w.writerows(found)
    report = {"sites_checked": len(sites), "valid_feeds_found": len(found), "inoreader_subscriptions_added": 0}
    (output / "feed-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))

if __name__ == "__main__":
    main()
