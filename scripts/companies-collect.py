#!/usr/bin/env python3
"""EXP-012 companies collector CLI.

At G3 (18.10.2026) only the local SQLite store is implemented: no Supabase or Notion writes.

Examples:
  python3 scripts/companies-collect.py --db /tmp/companies.sqlite3 --sources ted,usaspending_subawards --limit 10
  python3 scripts/companies-collect.py --db /tmp/companies.sqlite3 --legacy-snapshot <pages_dir>   # local only, private
  python3 scripts/companies-collect.py --db /tmp/companies.sqlite3 --gleif 20 --report
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from collectors.companies import pipeline, report  # noqa: E402
from collectors.companies.sources import gleif, legacy_notion, prozorro, ted, usaspending  # noqa: E402
from collectors.companies.sources.http import JsonHttp  # noqa: E402
from collectors.companies.store import SqliteStore  # noqa: E402

ADAPTERS = {
    "usaspending_recipients": lambda: usaspending.Recipients(),
    "usaspending_subawards": lambda: usaspending.Subawards(),
    "ted": lambda: ted.Awards(),
    "prozorro": lambda: prozorro.Awards(),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="local SQLite file (Supabase store is not enabled at G3)")
    ap.add_argument("--sources", default="", help="comma list: " + ",".join(ADAPTERS))
    ap.add_argument("--limit", type=int, default=10, help="records per source per run")
    ap.add_argument("--legacy-snapshot", help="pages dir of the private Notion snapshot (local only)")
    ap.add_argument("--gleif", type=int, default=0, help="max companies to enrich via GLEIF")
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args(argv)

    store = SqliteStore(args.db)
    http = JsonHttp(pause=0.3)
    results = {}
    if args.legacy_snapshot:
        rows = legacy_notion.load_snapshot(args.legacy_snapshot)
        results["legacy_notion"] = pipeline.run_source(store, legacy_notion.Legacy(rows), None, len(rows))
    for name in filter(None, args.sources.split(",")):
        if name not in ADAPTERS:
            ap.error(f"unknown source {name}")
        results[name] = pipeline.run_source(store, ADAPTERS[name](), http, args.limit)
    if args.gleif:
        results["gleif"] = pipeline.enrich_identities(store, gleif.Gleif(http), args.gleif)
    if args.report:
        results["summary"] = report.summary(store)
    print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    return 1 if any(isinstance(r, dict) and r.get("status") == "error" for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
