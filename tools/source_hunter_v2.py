#!/usr/bin/env python3
"""Source Hunter v2: bounded, resumable, relevance-aware public feed discovery.

No production writes or automatic subscriptions. Import earlier artifacts using --resume.
"""
import argparse
import csv
import json
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

import feedparser
import requests
from bs4 import BeautifulSoup
from source_hunter_1000 import SEEDS, HINTS, seed_urls

UA = "BOHUN-Source-Discovery/2.0 (public-feed-research)"
POSITIVE = ("defen", "defence", "defense", "militar", "naval", "aerospace",
            "procurement", "tender", "contract", "dual-use", "drone", "unmanned",
            "security", "innovation", "funding", "grant", "investment",
            "обор", "військ", "zbrojen", "verteidig", "défense")
NEGATIVE = ("recipe", "celebrity", "fashion", "sports betting", "coupon")
CATALOG_HINTS = ("member", "exhibitor", "supplier", "partner", "portfolio", "association")
MAX_BYTES = 1500000

def normalized(url):
    p = urlsplit(url.strip())
    if p.scheme not in ("https", "http") or not p.hostname:
        return ""
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", p.query, ""))

def hostname(url):
    return (urlsplit(url).hostname or "").lower()

def fetch(session, url):
    try:
        r = session.get(url, timeout=(3, 6), stream=True, allow_redirects=True)
        if r.status_code != 200:
            r.close()
            return None
        if int(r.headers.get("Content-Length", 0)) > MAX_BYTES:
            r.close()
            return None
        chunks, total = [], 0
        for chunk in r.iter_content(32768):
            total += len(chunk)
            if total > MAX_BYTES:
                r.close()
                return None
            chunks.append(chunk)
        r._content = b"".join(chunks)
        return r
    except (requests.RequestException, ValueError):
        return None

def scan(url):
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    # robots.txt is retrieved with bounded HTTP timeouts; failure means skip.
    root = urlsplit(url)
    robots_url = root.scheme + "://" + root.netloc + "/robots.txt"
    robots_response = fetch(session, robots_url)
    if robots_response is None:
        return {"site": url, "status": "robots_unavailable", "feeds": [], "related": []}
    from urllib.robotparser import RobotFileParser
    rp = RobotFileParser()
    rp.parse(robots_response.text.splitlines())
    if not rp.can_fetch(UA, url):
        return {"site": url, "status": "robots_disallowed", "feeds": [], "related": []}
    response = fetch(session, url)
    if response is None:
        return {"site": url, "status": "unavailable", "feeds": [], "related": []}
    soup = BeautifulSoup(response.content, "html.parser")
    candidates = set()
    related = []
    for tag in soup.select("link[rel][href]"):
        if "alternate" in tag.get("rel", []) and any(x in tag.get("type", "") for x in ("rss", "atom")):
            candidates.add(urljoin(response.url, tag["href"]))
    for suffix in HINTS[:6]:
        candidates.add(urljoin(root.scheme + "://" + root.netloc + "/", suffix.lstrip("/")))
    for a in soup.select("a[href]")[:1000]:
        target = normalized(urljoin(response.url, a["href"]))
        if not target:
            continue
        label = (a.get_text(" ", strip=True) + " " + target).lower()
        if hostname(target) == hostname(response.url):
            if any(x in label for x in ("rss", "atom", "/feed", "/news", "/press")):
                candidates.add(target)
        elif len(related) < 30 and any(x in label for x in POSITIVE + CATALOG_HINTS):
            related.append(target)
    feeds = []
    for target in sorted(candidates)[:14]:
        if not rp.can_fetch(UA, target) and hostname(target) == hostname(url):
            continue
        # Do not follow candidate feeds to third-party hosts without checking their robots.
        if hostname(target) != hostname(url):
            continue
        r = fetch(session, target)
        if r is None:
            continue
        parsed = feedparser.parse(r.content)
        if not parsed.version or not (parsed.entries or parsed.feed.get("title")):
            continue
        title = parsed.feed.get("title", "")
        sample = " ".join([title, parsed.feed.get("subtitle", "")] +
                          [str(e.get("title", "")) for e in parsed.entries[:8]]).lower()
        score = sum(1 for term in POSITIVE if term in sample) - 2 * sum(1 for term in NEGATIVE if term in sample)
        feeds.append({"feed_url": normalized(r.url), "title": title, "site": url,
                      "entries_seen": len(parsed.entries), "relevance_score": score,
                      "review_status": "candidate" if score > 0 else "needs_review"})
    return {"site": url, "status": "scanned", "feeds": feeds, "related": related}

def load_csv(path):
    if not path or not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))

def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

def save(out, records, feeds, queue, seen, args):
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "sources.csv", records, ["site", "status", "feed_count"])
    write_csv(out / "validated-feeds.csv", feeds.values(),
              ["feed_url", "title", "site", "entries_seen", "relevance_score", "review_status"])
    write_csv(out / "pending-sites.csv", ({"url": x} for x in queue), ["url"])
    (out / "checkpoint.json").write_text(json.dumps({"seen_hosts": sorted(seen)}), encoding="utf-8")
    opml = ET.Element("opml", version="2.0")
    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = "BOHUN Source Hunter v2 candidates (review required)"
    body = ET.SubElement(opml, "body")
    for f in feeds.values():
        if f["review_status"] != "candidate":
            continue
        ET.SubElement(body, "outline", {"type": "rss", "text": f["title"] or hostname(f["feed_url"]),
                                       "xmlUrl": f["feed_url"], "htmlUrl": f["site"]})
    ET.ElementTree(opml).write(out / "candidate-feeds.opml", encoding="utf-8", xml_declaration=True)
    report = {"target": args.target, "sites_checked_total": len(records),
              "technically_valid_feeds_total": len(feeds),
              "keyword_candidates_unreviewed": sum(f["review_status"] == "candidate" for f in feeds.values()),
              "queued_remaining": len(queue), "inoreader_subscriptions_added": 0,
              "production_writes": 0, "resume_supported": True}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--minutes", type=float, default=17)
    p.add_argument("--workers", type=int, default=12)
    p.add_argument("--target", type=int, default=1000)
    p.add_argument("--resume", default="")
    p.add_argument("--output", default="output/source-hunter-v2")
    args = p.parse_args()
    out = Path(args.output)
    old = Path(args.resume) if args.resume else None
    records = load_csv(old / "sources.csv") if old else []
    feeds = {r["feed_url"]: r for r in load_csv(old / "validated-feeds.csv")} if old else {}
    seen = {hostname(r["site"]) for r in records}
    if old and (old / "checkpoint.json").exists():
        seen.update(json.loads((old / "checkpoint.json").read_text()).get("seen_hosts", []))
    candidates = [r["url"] for r in load_csv(old / "pending-sites.csv")] if old else []
    candidates.extend(seed_urls())
    queue = deque(dict.fromkeys(filter(normalized, candidates)))
    deadline = time.monotonic() + args.minutes * 60
    cycles = 0
    try:
        while queue and len(feeds) < args.target and time.monotonic() < deadline - 45:
            batch = []
            while queue and len(batch) < args.workers:
                url = queue.popleft()
                h = hostname(url)
                if h and h not in seen:
                    seen.add(h)
                    batch.append(url)
            if not batch:
                break
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                for fut in as_completed([pool.submit(scan, u) for u in batch]):
                    try:
                        result = fut.result()
                    except Exception as exc:
                        result = {"site": "unknown", "status": type(exc).__name__, "feeds": [], "related": []}
                    records.append({"site": result["site"], "status": result["status"],
                                    "feed_count": len(result["feeds"])})
                    for feed in result["feeds"]:
                        if feed["feed_url"]:
                            feeds.setdefault(feed["feed_url"], feed)
                    for url in result["related"]:
                        h = hostname(url)
                        if h and h not in seen and len(queue) < 15000:
                            queue.append(url)
            cycles += 1
            print(json.dumps({"cycle": cycles, "sites": len(records), "feeds": len(feeds),
                              "queued": len(queue)}), flush=True)
            if cycles % 3 == 0:
                save(out, records, feeds, queue, seen, args)
    finally:
        save(out, records, feeds, queue, seen, args)

if __name__ == "__main__":
    main()
