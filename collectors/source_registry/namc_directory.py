#!/usr/bin/env python3
"""Discover *candidate* publisher websites from public defence industry directories.

Nothing is subscribed or published. Association membership is NOT evidence of
procurement, defence manufacturing, an RSS feed, or source fitness.
"""
import argparse
import csv
import json
import re
import time
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser
from pathlib import Path

UA = "Bohundefence-SourcesResearch/0.1 (public directory; contact site owner)"
NAMC = "https://www.namconsortium.org/membership/member-directory"
GICAT = "https://gicat.com/en/annuaire/"
AIA = "https://www.aia-aerospace.org/membership/our-members/"
DII = "https://www.dii.org/membership/member-companies"
REJECT_TLDS = (".ru", ".by", ".cn", ".kp")
EXCLUDE = {"facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
           "youtube.com", "google.com", "googletagmanager.com", "namconsortium.org",
           "gicat.com", "aia-aerospace.org", "dii.org"}

def domain(url):
    host = (urllib.parse.urlsplit(url).hostname or "").lower().removeprefix("www.")
    if not host or "." not in host or host.endswith(REJECT_TLDS):
        return ""
    if host in EXCLUDE or any(host.endswith("." + x) for x in EXCLUDE):
        return ""
    return host

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._anchor = None
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            a = dict(attrs)
            self._anchor = {"href": a.get("href", ""), "text": ""}
    def handle_data(self, data):
        if self._anchor is not None:
            self._anchor["text"] += data
    def handle_endtag(self, tag):
        if tag == "a" and self._anchor is not None:
            self.links.append(self._anchor)
            self._anchor = None

def read_page(url):
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https":
        raise ValueError("non-HTTPS directory")
    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(parts.scheme + "://" + parts.netloc + "/robots.txt")
    try:
        robots.read()
        if not robots.can_fetch(UA, url):
            raise PermissionError("robots disallow")
    except PermissionError:
        raise
    except Exception as exc:
        raise RuntimeError("robots unavailable: " + type(exc).__name__)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=35) as resp:
        if urllib.parse.urlsplit(resp.geturl()).hostname != parts.hostname:
            raise RuntimeError("redirect to another host")
        data = resp.read(2_000_001)
        if len(data) > 2_000_000:
            raise RuntimeError("HTML exceeds 2MB limit")
        return data.decode("utf-8", "replace")

def namc_items(html, page_url):
    parser = Links()
    parser.feed(html)
    items = {}
    for link in parser.links:
        if "visit website" not in link["text"].strip().casefold():
            continue
        absolute = urllib.parse.urljoin(page_url, link["href"])
        host = domain(absolute)
        if host:
            items[host] = {"name": host, "url": absolute, "hostname": host,
                           "directory": "NAMC", "discovery_country": "US",
                           "directory_page": page_url, "status": "candidate_unverified",
                           "delivery": "rss_atom_api_or_web_pending"}
    return list(items.values())

def fetch_namc(max_pages=60, sleep=0.8):
    results = {}
    failures = []
    total_announced = None
    url = NAMC
    seen_pages = set()
    for page in range(max_pages):
        if url in seen_pages:
            failures.append({"page": page, "problem": "pagination_cycle"})
            break
        seen_pages.add(url)
        try:
            html = read_page(url)
            match = re.search(r"Showing\s+\d+\s*-\s*\d+\s*\(of\s*([\d,]+)\)", html, re.I)
            if match:
                total_announced = int(match.group(1).replace(",", ""))
            items = namc_items(html, url)
            if not items:
                failures.append({"page": page, "problem": "no_visit_website_links"})
                break
            previous_count = len(results)
            for item in items:
                results.setdefault(item["hostname"], item)
            print(f"NAMC page={page+1} found={len(items)} new={len(results)-previous_count}", flush=True)
            if len(results) == previous_count:
                failures.append({"page": page, "problem": "repeated_or_duplicate_page"})
                break
            pager = Links()
            pager.feed(html)
            next_links = [x for x in pager.links if "next page" in x["text"].strip().casefold()]
            if not next_links:
                break
            following = urllib.parse.urljoin(url, next_links[-1]["href"])
            if urllib.parse.urlsplit(following).hostname != urllib.parse.urlsplit(NAMC).hostname:
                failures.append({"page": page, "problem": "invalid_next_page_host"})
                break
            url = following
        except Exception as exc:
            failures.append({"page": page, "problem": type(exc).__name__})
            break
        time.sleep(sleep)
    return list(results.values()), failures, total_announced

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="output/directory-source-candidates")
    p.add_argument("--pages", type=int, default=50)
    p.add_argument("--minimum", type=int, default=450)
    args = p.parse_args()
    if not 1 <= args.pages <= 60 or not 0 <= args.minimum <= 1000:
        p.error("pages must be 1..60, minimum 0..1000")
    items, failures, announced = fetch_namc(args.pages)
    base = Path(args.output)
    base.parent.mkdir(parents=True, exist_ok=True)
    fields = ["name", "url", "hostname", "directory", "discovery_country",
              "directory_page", "status", "delivery"]
    with base.with_suffix(".csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(items)
    summary = {"source": NAMC, "reported_directory_member_count": announced,
               "extracted_distinct_candidate_websites": len(items),
               "errors": failures, "status": "unverified_websites_no_feed_checks",
               "note": "Association membership != defence contract or running feed",
               "supplementary_unharvested_catalogs": [GICAT, AIA, DII]}
    base.with_suffix(".json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print("RESULT " + json.dumps(summary))
    if len(items) < args.minimum:
        raise SystemExit("Coverage below minimum: review extraction report, do not claim 500")

if __name__ == "__main__":
    main()
