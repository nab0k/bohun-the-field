#!/usr/bin/env python3
"""Offline OPML inventory and deduplication. No Inoreader API calls."""
import argparse
import csv
import json
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

def canonical(url):
    p = urllib.parse.urlsplit((url or "").strip())
    host = (p.hostname or "").lower()
    path = p.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((p.scheme.lower(), host + ((":" + str(p.port)) if p.port else ""), path, "", ""))

def read_opml(path):
    root = ET.parse(path).getroot()
    body = root.find("body")
    if body is None:
        raise ValueError("OPML body missing")
    rows = []
    def visit(node, folders):
        for item in node.findall("outline"):
            feed = item.get("xmlUrl")
            if feed:
                rows.append({"title": item.get("title") or item.get("text") or "", "feed_url": feed,
                             "canonical_url": canonical(feed), "folder": " / ".join(folders),
                             "website": item.get("htmlUrl") or ""})
            else:
                visit(item, folders + [item.get("title") or item.get("text") or "(unnamed)"])
    visit(body, [])
    return rows

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("opml", type=Path)
    ap.add_argument("--out", type=Path, default=Path("output/opml-audit"))
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    rows = read_opml(args.opml)
    counts = Counter(r["canonical_url"] for r in rows)
    folders = Counter(r["folder"] for r in rows)
    for r in rows:
        r["duplicate_count"] = counts[r["canonical_url"]]
    with (args.out / "subscriptions.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["title", "feed_url", "canonical_url", "folder", "website", "duplicate_count"])
        writer.writeheader()
        writer.writerows(rows)
    report = {"subscriptions": len(rows), "unique_feed_urls": len(counts),
              "duplicate_feed_urls": {k: v for k, v in counts.items() if v > 1},
              "folders": dict(folders), "folder_names_repeated_across_paths": dict(
                  (name, n) for name, n in Counter(p.split(" / ")[-1] for p in folders).items() if n > 1)}
    (args.out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("No network calls; no subscription changes.")

if __name__ == "__main__":
    main()
