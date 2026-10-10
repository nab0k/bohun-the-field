#!/usr/bin/env python3
"""Discover RSS/Atom for a source registry and optionally subscribe via Inoreader OAuth.

Dry-run by default. Never stores credentials or modifies existing subscriptions.
Only --apply performs writes, and only for validated RSS/Atom endpoints.
"""
import argparse
import concurrent.futures
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

try:
    from .sync import access_token, api
except ImportError:
    from sync import access_token, api

UA = "Bohundefence-FeedDiscovery/0.1"
MAX_BYTES = 1_000_000
FOLDERS = {
    "Media / intelligence": "BOHUN_MEDIA",
    "Governments / procurement / regulation": "BOHUN_GOV",
    "Industry / corporates": "BOHUN_INDUSTRY",
    "Investments / accelerators / aid / events": "BOHUN_OPPORTUNITIES",
    "Additional sources — completion to 100": "BOHUN_RESEARCH",
}

class FeedLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.feeds = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "link" and "alternate" in a.get("rel", "").lower().split():
            if a.get("type", "").lower() in ("application/rss+xml", "application/atom+xml") and a.get("href"):
                self.feeds.append(a["href"])

def safe_https(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme == "https" and bool(p.hostname) and not p.username and not p.password and p.hostname not in ("localhost", "127.0.0.1", "::1")

def fetch(url):
    if not safe_https(url):
        raise ValueError("HTTPS public URL required")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml,application/atom+xml,text/html,application/xml"})
    with urllib.request.urlopen(req, timeout=12) as response:
        final = response.geturl()
        if not safe_https(final):
            raise ValueError("unsafe redirect")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("oversized response")
        return data, final, response.headers.get("Content-Type", "")

def is_feed(data):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return False
    tag = root.tag.lower()
    return (tag == "rss" and root.find("./channel/item") is not None) or ((tag.endswith("}feed") or tag == "feed") and any(child.tag.lower().endswith("entry") for child in root)) or ((tag.endswith("}rdf") or tag == "rdf") and any(child.tag.lower().endswith("item") for child in root))

def discover(source):
    homepage = source["url"]
    result = {**source, "status": "no_valid_feed", "feed_url": None}
    try:
        data, final, _ = fetch(homepage)
        if is_feed(data):
            result.update(status="validated_feed", feed_url=final)
            return result
        parser = FeedLinks()
        parser.feed(data.decode("utf-8", errors="replace"))
        base = urllib.parse.urlsplit(final)
        candidates = [urllib.parse.urljoin(final, x) for x in parser.feeds]
        # Conventional endpoints are guesses, not validated until parsed.
        origin = base.scheme + "://" + base.netloc
        candidates += [origin + x for x in ("/feed", "/rss", "/rss.xml", "/feed.xml", "/atom.xml")]
        for candidate in dict.fromkeys(candidates):
            if not safe_https(candidate):
                continue
            if urllib.parse.urlsplit(candidate).hostname != base.hostname:
                continue
            try:
                feed, feed_final, _ = fetch(candidate)
                if urllib.parse.urlsplit(feed_final).hostname == base.hostname and is_feed(feed):
                    result.update(status="validated_feed", feed_url=feed_final)
                    return result
            except (ValueError, urllib.error.URLError, TimeoutError, OSError):
                continue
    except (ValueError, urllib.error.URLError, TimeoutError, OSError):
        result["status"] = "site_unreachable"
    return result

def post_api(path, token, values):
    url = "https://www.inoreader.com/reader/api/0/" + path
    data = urllib.parse.urlencode(values).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
        headers={"Authorization": "Bearer " + token, "User-Agent": UA,
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read(100000).decode("utf-8", errors="replace")
        if response.status < 200 or response.status >= 300:
            raise RuntimeError("API non-2xx response")
        return raw

def existing_ids(token):
    subs = api("subscription/list", token).get("subscriptions", [])
    return {s.get("id") for s in subs if s.get("id")}, len(subs)

def run(args):
    registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
    sources = registry["sources"] if isinstance(registry, dict) else registry
    if not isinstance(sources, list):
        raise ValueError("registry must contain sources list")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(discover, sources))
    token = access_token() if args.apply or args.check_subscriptions else None
    ids, current_count = existing_ids(token) if token else (set(), None)
    selected = {name.strip().casefold() for name in args.only.split(",") if name.strip()}
    added = 0
    for item in results:
        feed = item.get("feed_url")
        feed_id = "feed/" + feed if feed else None
        item["folder"] = FOLDERS.get(item["group"], "BOHUN_RESEARCH")
        if selected and item["name"].casefold() not in selected:
            item["status"] = "not_selected"
            continue
        if feed_id and feed_id in ids:
            item["status"] = "already_subscribed"
        elif args.apply and feed:
            if added >= args.max_add:
                item["status"] = "pending_limit"
                continue
            try:
                response = post_api("subscription/quickadd", token, {"quickadd": feed})
                # quickadd must return an identifiable feed id; do not assume 200 means success.
                try:
                    payload = json.loads(response)
                except json.JSONDecodeError:
                    payload = {}
                created_id = payload.get("streamId") or payload.get("id")
                if not created_id or not str(created_id).startswith("feed/"):
                    item["status"] = "add_unconfirmed"
                    continue
                ids.add(created_id)
                added += 1
                item["status"] = "added"
                try:
                    post_api("subscription/edit", token, {"s": created_id, "a": "user/-/label/" + item["folder"], "ac": "edit"})
                    item["folder_status"] = "assigned"
                except (urllib.error.URLError, RuntimeError):
                    item["folder_status"] = "assignment_failed"
            except (urllib.error.URLError, RuntimeError):
                item["status"] = "add_failed"
    # Reconcile with the API after writes; never count HTTP 200 alone as confirmed.
    if args.apply:
        verified_ids, final_count = existing_ids(token)
        for item in results:
            if item["status"] == "added" and "feed/" + item["feed_url"] not in verified_ids:
                item["status"] = "added_needs_verification"
    else:
        final_count = current_count
    summary = {"candidates": len(results), "validated_feeds": sum(bool(x["feed_url"]) for x in results),
               "already_subscribed": sum(x["status"] == "already_subscribed" for x in results),
               "added": sum(x["status"] == "added" for x in results),
               "subscriptions_before": current_count, "subscriptions_after": final_count,
               "mode": "apply" if args.apply else "dry-run"}
    report = {"summary": summary, "results": results}
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print("REPORT " + args.report)
    if args.apply and any(x["status"] in ("add_failed", "add_unconfirmed", "added_needs_verification", "assignment_failed") or x.get("folder_status") == "assignment_failed" for x in results):
        return 2
    return 0

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", default=str(Path(__file__).with_name("top100_sources.json")))
    p.add_argument("--report", default="inoreader-source-report.json")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--only", default="", help="Comma-separated exact source names to import; leave empty for all")
    p.add_argument("--check-subscriptions", action="store_true")
    p.add_argument("--max-add", type=int, default=20)
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()
    if args.apply and not args.only.strip():
        p.error("--apply requires --only to prevent accidental mass import")
    if not 1 <= args.workers <= 8 or not 1 <= args.max_add <= 100:
        p.error("workers must be 1..8 and max-add must be 1..100")
    return run(args)

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, urllib.error.URLError) as exc:
        print("FAILED: " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        sys.exit(1)
