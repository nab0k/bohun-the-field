#!/usr/bin/env python3
"""Reconcile candidate publishers against a timestamped Inoreader discovery report.

The output reports observed technical coverage, NOT a live subscription guarantee.
"""
import argparse
import csv
import json
from pathlib import Path
from urllib.parse import urlsplit

def normalized_host(url):
    host = (urlsplit(url or "").hostname or "").lower()
    return host.removeprefix("www.")

def build(sources, report, channels):
    observations = {row["name"]: row for row in report.get("results", [])}
    web_by_host = {}
    for channel in channels:
        host = normalized_host(channel.get("url"))
        if host:
            web_by_host.setdefault(host, []).append(channel.get("url"))
    out = []
    for source in sources:
        name, homepage = source["name"], source["url"]
        obs = observations.get(name, {})
        status = obs.get("status", "not_checked")
        out.append({
            "name": name,
            "group": source.get("group", ""),
            "website": homepage,
            "publisher_host": normalized_host(homepage),
            "inoreader_observed_status": status,
            "feed_url": obs.get("feed_url") or "",
            "web_monitor_seed_urls": "|".join(web_by_host.get(normalized_host(homepage), [])),
            "web_monitor_state": "unverified_seed" if normalized_host(homepage) in web_by_host else "not_configured",
            "api_state": "not_reconciled",
            "next_action": "verify_inoreader_live" if status in ("already_subscribed", "added") else (
                "review_feed_for_import" if status == "validated_feed" else "find_other_delivery_method"),
        })
    return out

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sources", default="collectors/inoreader/top100_sources.json")
    p.add_argument("--inoreader-report", required=True)
    p.add_argument("--channels", default="collectors/web_monitor/channels.json")
    p.add_argument("--extra-candidates", default="docs/sources/global-source-candidates-20261009.csv")
    p.add_argument("--out", default="output/unified-source-registry.csv")
    args = p.parse_args()
    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))["sources"]
    # Expand beyond Notion Top100 with GitHub's 84-source candidate catalog.
    # Deduplication is by host only, and does not imply equal publisher or feed.
    known_hosts = {normalized_host(src["url"]) for src in sources}
    extra_added = 0
    with Path(args.extra_candidates).open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            host = normalized_host(row.get("url"))
            if not host or host in known_hosts:
                continue
            known_hosts.add(host)
            sources.append({"name": row["name"], "url": row["url"],
                            "group": "Extra candidate / " + row.get("category", "")})
            extra_added += 1
    report = json.loads(Path(args.inoreader_report).read_text(encoding="utf-8"))
    channels = json.loads(Path(args.channels).read_text(encoding="utf-8"))
    rows = build(sources, report, channels)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"REGISTRY extra_candidates={extra_added} sources={len(rows)} inoreader_observed={sum(bool(r['feed_url']) for r in rows)} web_seeds={sum(bool(r['web_monitor_seed_urls']) for r in rows)}")
    # This is a snapshot, not evidence that a live Inoreader folder or monitoring job is active.

if __name__ == "__main__":
    main()
