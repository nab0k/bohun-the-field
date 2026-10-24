#!/usr/bin/env python3
"""Discover RSS/Atom for a source registry and optionally subscribe via Inoreader OAuth.

Dry-run by default. Never stores credentials or modifies existing subscriptions.
Only --apply performs writes, and only for validated RSS/Atom endpoints.
"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
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

def subscriptions_snapshot(token):
    payload = api("subscription/list", token)
    subs = payload.get("subscriptions") if isinstance(payload, dict) else None
    if not isinstance(subs, list):
        raise RuntimeError("Subscription API did not return a list")
    snapshot = {}
    for sub in subs:
        if not isinstance(sub, dict) or not isinstance(sub.get("id"), str) or not sub["id"]:
            raise RuntimeError("Invalid subscription identity")
        categories = sub.get("categories", [])
        if not isinstance(categories, list) or any(not isinstance(c, dict) for c in categories):
            raise RuntimeError("Invalid subscription categories")
        if sub["id"] in snapshot:
            raise RuntimeError("Duplicate subscription identity")
        snapshot[sub["id"]] = sub
    return snapshot

def existing_ids(token):
    snapshot = subscriptions_snapshot(token)
    return set(snapshot), len(snapshot)

def has_folder(sub, folder):
    return any(c.get("label") == folder or
               (isinstance(c.get("id"), str) and c["id"].endswith("/label/" + folder))
               for c in sub.get("categories", []))

API_ERRORS = (urllib.error.URLError, RuntimeError, ValueError, TimeoutError, OSError)

def run(args):
    registry_bytes = Path(args.registry).read_bytes()
    registry = json.loads(registry_bytes)
    sources = registry.get("sources") if isinstance(registry, dict) else registry
    if not isinstance(sources, list):
        raise ValueError("registry must contain sources list")
    if not 1 <= args.workers <= 8 or not 1 <= args.max_add <= 100:
        raise ValueError("workers must be 1..8 and max-add must be 1..100")
    if any(not isinstance(s, dict) or not isinstance(s.get("name"), str) or
           not s["name"].strip() for s in sources):
        raise ValueError("Each source needs a non-empty name")
    selected = {name.strip().casefold() for name in args.only.split(",") if name.strip()}
    if args.apply and not selected:
        raise ValueError("--apply requires --only")
    known = {s["name"].strip().casefold() for s in sources}
    if selected - known:
        raise ValueError("--only contains unknown source names")
    indexes = [i for i, s in enumerate(sources)
               if not selected or s["name"].strip().casefold() in selected]
    if args.apply and not indexes:
        raise ValueError("No sources selected for apply")
    for i in indexes:
        source = sources[i]
        if not isinstance(source.get("url"), str) or not safe_https(source["url"]):
            raise ValueError("Selected source requires a public HTTPS URL")
        if not isinstance(source.get("group"), str):
            raise ValueError("Selected source requires a group")
        if args.apply and source["group"] not in FOLDERS:
            raise ValueError("Unknown source group in apply")
    # Selection must bound discovery as well as subscription writes.
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        discovered = dict(zip(indexes, pool.map(discover, (sources[i] for i in indexes))))
    results = [discovered.get(i, {**s, "status": "not_selected", "feed_url": None})
               for i, s in enumerate(sources)]
    seen = {}
    stop_reason = None
    for i, item in enumerate(results):
        item["folder"] = FOLDERS.get(item.get("group"), "BOHUN_RESEARCH")
        item["membership_verified"] = None
        item["folder_verified"] = None
        feed = item.get("feed_url")
        if not feed:
            continue
        if not isinstance(feed, str) or not safe_https(feed):
            item["status"] = "invalid_feed_url"
            item["feed_url"] = None
            continue
        if feed in seen:
            first = results[seen[feed]]
            if first["folder"] != item["folder"]:
                first["status"] = item["status"] = "identity_conflict"
                stop_reason = "identity_conflict"
            else:
                item["status"] = "duplicate_input"
                item["duplicate_of_index"] = seen[feed]
        else:
            seen[feed] = i

    snapshot = None
    current_count = final_count = None
    token = None
    if not stop_reason and (args.apply or args.check_subscriptions):
        try:
            token = access_token()
        except API_ERRORS as exc:
            stop_reason = "oauth_failed"
            auth_error_type = type(exc).__name__
        else:
            try:
                snapshot = subscriptions_snapshot(token)
                current_count = final_count = len(snapshot)
            except API_ERRORS:
                stop_reason = "initial_snapshot_failed"

    def read_back(item):
        nonlocal snapshot, final_count
        try:
            fresh = subscriptions_snapshot(token)
        except API_ERRORS as exc:
            item["readback_error_type"] = type(exc).__name__
            item["membership_verified"] = item["folder_verified"] = None
            snapshot = None
            final_count = None
            return False
        snapshot = fresh
        final_count = len(fresh)
        sub = fresh.get("feed/" + item["feed_url"])
        item["membership_verified"] = sub is not None
        item["folder_verified"] = sub is not None and has_folder(sub, item["folder"])
        return True

    attempts = folder_attempts = 0
    for item in results:
        if item["status"] in ("not_selected", "duplicate_input", "identity_conflict") or not item.get("feed_url"):
            continue
        if stop_reason:
            item["status"] = "pending_after_failure"
            continue
        feed = item["feed_url"]
        feed_id = "feed/" + feed
        if snapshot is not None and feed_id in snapshot:
            item["membership_verified"] = True
            item["folder_verified"] = has_folder(snapshot[feed_id], item["folder"])
            if item["folder_verified"]:
                item["status"] = "already_subscribed"
                item["folder_status"] = "already_assigned"
            else:
                item["status"] = "connection_incomplete"
                item["folder_status"] = "missing"
                if args.apply:
                    stop_reason = "existing_folder_missing"
            continue
        if not args.apply:
            continue
        if attempts >= args.max_add:
            item["status"] = "pending_limit"
            continue
        # Count the attempted write before sending: an unknown response may have created a feed.
        attempts += 1
        try:
            response = post_api("subscription/quickadd", token, {"quickadd": feed})
            payload = json.loads(response)
            created_id = (payload.get("streamId") or payload.get("id")) if isinstance(payload, dict) else None
        except API_ERRORS as exc:
            item["write_error_type"] = type(exc).__name__
            created_id = None
        if created_id != feed_id:
            item["status"] = "add_unconfirmed"
            item["folder_status"] = "not_requested"
            stop_reason = "add_outcome_unknown"
            read_back(item)
            continue
        folder_attempts += 1
        try:
            post_api("subscription/edit", token,
                     {"s": feed_id, "a": "user/-/label/" + item["folder"], "ac": "edit"})
        except API_ERRORS as exc:
            item["status"] = "added_needs_verification"
            item["folder_status"] = "assignment_failed"
            item["write_error_type"] = type(exc).__name__
            stop_reason = "folder_outcome_unknown"
            read_back(item)
            continue
        if not read_back(item):
            item["status"] = "added_needs_verification"
            item["folder_status"] = "not_verified"
            stop_reason = "readback_failed"
        elif not item["membership_verified"] or not item["folder_verified"]:
            item["status"] = "added_needs_verification"
            item["folder_status"] = "not_confirmed"
            stop_reason = "connection_not_confirmed"
        else:
            item["status"] = "added"
            item["folder_status"] = "assigned"

    # Check all earlier successes against the most recent full snapshot, too.
    for item in results:
        if item["status"] not in ("added", "already_subscribed"):
            continue
        if snapshot is None:
            item["membership_verified"] = item["folder_verified"] = None
        else:
            sub = snapshot.get("feed/" + item["feed_url"])
            item["membership_verified"] = sub is not None
            item["folder_verified"] = sub is not None and has_folder(sub, item["folder"])
        if not item["membership_verified"] or not item["folder_verified"]:
            item["status"] = "connection_incomplete"
            item["folder_status"] = "not_verified"
            stop_reason = stop_reason or "final_state_mismatch"
    summary = {"candidates": len(results), "validated_feeds": sum(bool(x["feed_url"]) for x in results),
               "selected": len(indexes), "selected_sources": [sources[i]["name"] for i in indexes],
               "already_subscribed": sum(x["status"] == "already_subscribed" for x in results),
               "added": sum(x["status"] == "added" for x in results),
               "add_attempts": attempts, "folder_edit_attempts": folder_attempts,
               "stop_reason": stop_reason,
               "subscriptions_before": current_count, "subscriptions_after": final_count,
               "mode": "apply" if args.apply else "dry-run"}
    report = {"summary": summary, "results": results,
              "registry_sha256": hashlib.sha256(registry_bytes).hexdigest(),
              "observed_at": dt.datetime.now(dt.timezone.utc).isoformat()}
    if stop_reason == "oauth_failed":
        report["auth_error_type"] = auth_error_type
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    print("REPORT " + args.report)
    if stop_reason or (args.apply and any(x["status"] not in
            ("added", "already_subscribed", "not_selected", "duplicate_input", "pending_limit") for x in results)):
        return 2
    return 0

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", default=str(Path(__file__).with_name("top100_sources.json")))
    p.add_argument("--report", default="inoreader-source-report.json")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--only", default="", help="Exact source names to discover/check; required for apply")
    p.add_argument("--check-subscriptions", action="store_true")
    p.add_argument("--max-add", type=int, default=20, help="Maximum quickadd attempts, including unknown outcomes")
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
