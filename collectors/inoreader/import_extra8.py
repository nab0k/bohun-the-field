#!/usr/bin/env python3
"""One-shot, idempotent Inoreader API import, verified against subscription/list.

Eight candidate feed URLs from uploaded expanded discovery dry-run report 2026-10-10. Feed
parsing alone does not prove publisher relevance. Never print OAuth tokens.
"""
import json
import sys
import time
import urllib.error
from pathlib import Path
from inoreader_source_manager import FOLDERS, post_api, fetch, is_feed
from sync import access_token, api

CANDIDATES = [
    [
        "industriemagazin.at",
        "https://industriemagazin.at/rss.xml",
        "Media / intelligence"
    ],
    [
        "msm.sk",
        "https://www.msm.sk/feed/",
        "Industry / corporates"
    ],
    [
        "militaeraktuell.at",
        "https://militaeraktuell.at/feed/",
        "Media / intelligence"
    ],
    [
        "drones-magazin.de",
        "https://www.drones-magazin.de/feed/",
        "Media / intelligence"
    ],
    [
        "govinfo.gov",
        "https://www.govinfo.gov/rss.xml",
        "Governments / procurement / regulation"
    ],
    [
        "trade.gov",
        "https://www.trade.gov/rss.xml",
        "Governments / procurement / regulation"
    ],
    [
        "agoria.be",
        "https://www.agoria.be/en/rss.xml",
        "Industry / corporates"
    ],
    [
        "oip.be",
        "https://www.oip.be/feed/",
        "Industry / corporates"
    ]
]

def subscriptions(token):
    result = api("subscription/list", token)
    if not isinstance(result.get("subscriptions"), list):
        raise RuntimeError("Subscription API did not return a list")
    return result["subscriptions"]

def find(subs, feed_url, stream_id=None):
    expected = "feed/" + feed_url
    return next((row for row in subs if row.get("id") in (expected, stream_id) or row.get("url") == feed_url), None)

def has_folder(sub, folder):
    return any(cat.get("label") == folder or cat.get("id", "").endswith("/label/" + folder)
               for cat in sub.get("categories", []))

def main():
    token = access_token()
    before = subscriptions(token)
    current = before
    results = []
    for name, feed_url, group in CANDIDATES:
        folder = FOLDERS[group]
        result = {"name": name, "feed_url": feed_url, "folder": folder,
                  "subscription_status": "pending", "folder_status": "not_checked"}
        try:
            sub = find(current, feed_url)
            if not sub:
                # Re-validate all original feed URLs immediately before API writes.
                try:
                    raw_feed, final_url, _ = fetch(feed_url)
                    if not is_feed(raw_feed):
                        result["subscription_status"] = "invalid_or_empty_feed"
                        results.append(result)
                        print(name + ": invalid_or_empty_feed", flush=True)
                        continue
                    from urllib.parse import urlsplit
                    if urlsplit(final_url).hostname != urlsplit(feed_url).hostname:
                        result["subscription_status"] = "redirected_to_other_host"
                        results.append(result)
                        print(name + ": redirected_to_other_host", flush=True)
                        continue
                except (ValueError, urllib.error.URLError, TimeoutError, OSError) as error:
                    result["subscription_status"] = "feed_unavailable_" + type(error).__name__
                    results.append(result)
                    print(name + ": " + result["subscription_status"], flush=True)
                    continue
                created_id = None
                try:
                    raw = post_api("subscription/quickadd", token, {"quickadd": feed_url})
                    # The response is informational: actual state comes from subscription/list.
                    try:
                        response = json.loads(raw)
                        result["quickadd_response_type"] = type(response).__name__
                        if isinstance(response, dict):
                            created_id = response.get("streamId") or response.get("id")
                    except ValueError:
                        result["quickadd_response_type"] = "non_json"
                except (urllib.error.URLError, RuntimeError) as error:
                    result["quickadd_error"] = type(error).__name__
                time.sleep(1.0)
                current = subscriptions(token)
                sub = find(current, feed_url, created_id)
                result["api_stream_id"] = created_id
                result["subscription_status"] = "confirmed_new" if sub else "unconfirmed"
            else:
                result["subscription_status"] = "confirmed_existing"
            if sub:
                stream_id = sub.get("id")
                if has_folder(sub, folder):
                    result["folder_status"] = "already_assigned"
                elif stream_id:
                    try:
                        post_api("subscription/edit", token,
                                 {"s": stream_id, "a": "user/-/label/" + folder, "ac": "edit"})
                        result["folder_status"] = "requested"
                    except (urllib.error.URLError, RuntimeError) as error:
                        result["folder_status"] = "failed_" + type(error).__name__
                else:
                    result["folder_status"] = "missing_feed_id"
        except (urllib.error.URLError, RuntimeError, ValueError) as error:
            result["subscription_status"] = "failed_" + type(error).__name__
        results.append(result)
        print(name + ": " + result["subscription_status"] + ", folder=" + result["folder_status"], flush=True)
        time.sleep(0.3)
    final = subscriptions(token)
    for result in results:
        verified = find(final, result["feed_url"], result.get("api_stream_id"))
        if not verified:
            result["subscription_status"] = "not_in_final_list"
            result["folder_status"] = "not_verified"
        else:
            result["folder_status"] = "confirmed" if has_folder(verified, result["folder"]) else "unconfirmed"
    summary = {
        "targeted": len(CANDIDATES), "before": len(before), "after": len(final),
        "confirmed_new": sum(r["subscription_status"] == "confirmed_new" for r in results),
        "confirmed_existing": sum(r["subscription_status"] == "confirmed_existing" for r in results),
        "confirmed_in_final_list": sum(r["subscription_status"] in ("confirmed_new", "confirmed_existing") for r in results),
        "folders_confirmed": sum(r["folder_status"] == "confirmed" for r in results),
        "unconfirmed": sum(r["subscription_status"] not in ("confirmed_new", "confirmed_existing") for r in results),
    }
    report = Path("output/inoreader-import-extra8.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({"summary": summary, "results": results}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("SUMMARY " + json.dumps(summary, ensure_ascii=False), flush=True)
    if summary["unconfirmed"] or summary["folders_confirmed"] != len(CANDIDATES):
        return 2
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, urllib.error.URLError) as error:
        print("FATAL " + type(error).__name__, file=sys.stderr)
        sys.exit(1)
