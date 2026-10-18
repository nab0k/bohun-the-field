#!/usr/bin/env python3
"""EXP-012 companies collector CLI.

Stores: local SQLite (--db) or Supabase schema `companies` (--store supabase, T08; password only from
SUPABASE_DB_PASSWORD). Nothing is written to Notion.

Examples:
  python3 scripts/companies-collect.py --db /tmp/companies.sqlite3 --sources ted,usaspending_subawards --limit 10
  python3 scripts/companies-collect.py --db /tmp/companies.sqlite3 --legacy-snapshot <pages_dir>   # local only, private
  python3 scripts/companies-collect.py --db /tmp/companies.sqlite3 --gleif 20 --report
  SUPABASE_DB_PASSWORD=... python3 scripts/companies-collect.py --store supabase --migrate --sources ted --limit 10
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
    ap.add_argument("--store", choices=("sqlite", "supabase"), default="sqlite")
    ap.add_argument("--db", help="local SQLite file (with --store sqlite)")
    ap.add_argument("--migrate", action="store_true", help="supabase: apply db/companies schema once if absent")
    ap.add_argument("--sample", type=int, default=0,
                    help="print N sample companies for manual review (public-source fields only, no Ukraine rows)")
    ap.add_argument("--sources", default="", help="comma list: " + ",".join(ADAPTERS))
    ap.add_argument("--limit", type=int, default=10, help="records per source per run")
    ap.add_argument("--legacy-snapshot", help="pages dir of the private Notion snapshot (local only)")
    ap.add_argument("--gleif", type=int, default=0, help="max companies to enrich via GLEIF")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--reset-staging", action="store_true",
                    help="supabase: DROP SCHEMA companies and re-create it (staging only; requires --migrate)")
    ap.add_argument("--replay", action="store_true",
                    help="start the given sources from their first page again (idempotency check, AC1)")
    args = ap.parse_args(argv)

    results = {}
    if args.store == "supabase":
        if args.legacy_snapshot:
            ap.error("--legacy-snapshot is private data: local SQLite only")
        from collectors.companies.pg_store import PgStore
        store = PgStore()
        if args.reset_staging:
            if not args.migrate:
                ap.error("--reset-staging requires --migrate")
            results["reset"] = store.reset_staging()
        if args.migrate:
            results["migration"] = store.migrate()
        elif not store.schema_exists():
            ap.error("schema companies is absent: run with --migrate")
    else:
        if not args.db:
            ap.error("--db is required with --store sqlite")
        if args.reset_staging:
            ap.error("--reset-staging is for the Supabase staging schema only")
        store = SqliteStore(args.db)
    http = JsonHttp(pause=0.3)
    if args.legacy_snapshot:
        rows = legacy_notion.load_snapshot(args.legacy_snapshot)
        results["legacy_notion"] = pipeline.run_source(store, legacy_notion.Legacy(rows), None, len(rows))
    for name in filter(None, args.sources.split(",")):
        if name not in ADAPTERS:
            ap.error(f"unknown source {name}")
        if args.replay:
            store.save_source_state(ADAPTERS[name]().source, None, 0, False)
            store.commit()
        results[name] = pipeline.run_source(store, ADAPTERS[name](), http, args.limit)
    if args.gleif:
        results["gleif"] = pipeline.enrich_identities(store, gleif.Gleif(http), args.gleif)
    if args.report:
        results["summary"] = report.summary(store)
    if args.sample:
        results["sample"] = report.sample(store, args.sample)
    print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    return 1 if any(isinstance(r, dict) and r.get("status") == "error" for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
