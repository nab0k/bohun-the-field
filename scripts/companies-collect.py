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
import hashlib
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from collectors.companies import cards, pipeline, report  # noqa: E402
from collectors.companies.sources import atla, edf, eurosatory, gleif, legacy_notion, prozorro, ted, usaspending  # noqa: E402
from collectors.companies.sources.http import JsonHttp  # noqa: E402
from collectors.companies.store import SqliteStore  # noqa: E402

ADAPTERS = {
    "usaspending_recipients": lambda: usaspending.Recipients(),
    "usaspending_subawards": lambda: usaspending.Subawards(),
    "ted": lambda: ted.Awards(),
    "prozorro": lambda: prozorro.Awards(),
    "atla": lambda: atla.Awards(),
    "edf": lambda: edf.Grants(),
    "eurosatory2024": lambda: eurosatory.Exhibitors2024(),
}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", choices=("sqlite", "supabase"), default="sqlite")
    ap.add_argument("--dry-run", action="store_true", help="use SQLite in-memory, no Supabase writes")
    ap.add_argument("--raw-dir", help="optional immutable raw HTML snapshot directory")
    ap.add_argument("--snapshot-file", help="offline Eurosatory HTML input, with --dry-run only")
    ap.add_argument("--expected-sha256", help="required SHA-256 verification for --snapshot-file")
    ap.add_argument("--db", help="local SQLite file (with --store sqlite)")
    ap.add_argument("--migrate", action="store_true", help="supabase: apply db/companies schema once if absent")
    ap.add_argument("--approve-evidence-migration-003-sha256", metavar="REVIEWED_SQL_SHA256",
                    help="migration-only; explicit approval of exact 003 SQL bytes (requires --store supabase --migrate)")
    ap.add_argument("--sample", type=int, default=0,
                    help="print N sample companies for manual review (public-source fields only, no Ukraine rows)")
    ap.add_argument("--sources", default="", help="comma list: " + ",".join(ADAPTERS))
    ap.add_argument("--limit", type=int, default=10, help="records per source per run")
    ap.add_argument("--legacy-snapshot", help="pages dir of the private Notion snapshot (local only)")
    ap.add_argument("--gleif", type=int, default=0, help="max companies to enrich via GLEIF")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--promote", action="store_true", help="apply the lead -> card rule (D02) to all companies")
    ap.add_argument("--benchmark", action="append", default=[],
                    help="benchmark JSON ({'entries': [...]}) for the completeness report; repeatable")
    ap.add_argument("--reset-staging", action="store_true",
                    help="supabase: DROP SCHEMA companies and re-create it (staging only; requires --migrate)")
    ap.add_argument("--replay", action="store_true",
                    help="start the given sources from their first page again (idempotency check, AC1)")
    args = ap.parse_args(argv)

    if args.approve_evidence_migration_003_sha256 is not None:
        # The explicit 003 invocation must NEVER collect, promote or reset as a side effect.
        if (args.store != "supabase" or not args.migrate or args.dry_run or
                args.reset_staging or args.sources or args.gleif or args.promote or
                args.replay or args.sample or args.report or args.benchmark or
                args.legacy_snapshot or args.raw_dir or args.snapshot_file or
                args.expected_sha256 or args.db):
            ap.error("003 requires migration-only --store supabase --migrate, no collection/other flags")

    if args.snapshot_file:
        if not args.dry_run or args.sources != "eurosatory2024" or not args.expected_sha256:
            ap.error("--snapshot-file needs --dry-run --sources eurosatory2024 --expected-sha256")
        expected = args.expected_sha256.lower()
        if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            ap.error("invalid --expected-sha256")
        raw = pathlib.Path(args.snapshot_file).read_bytes()
        from collectors.companies.sources.http import MAX_BYTES
        if len(raw) > MAX_BYTES:
            ap.error("snapshot exceeds existing HTTP client size limit")
        if hashlib.sha256(raw).hexdigest() != expected:
            ap.error("snapshot SHA-256 mismatch; refusing import")
    elif args.expected_sha256:
        ap.error("--expected-sha256 requires --snapshot-file")
    if args.dry_run and (args.store != "sqlite" or args.db or args.migrate or args.reset_staging
                         or args.promote or args.gleif or args.legacy_snapshot):
        ap.error("--dry-run permits SQLite in-memory only, without migrations/enrichment/promotion")
    if "eurosatory2024" in args.sources.split(",") and (args.promote or args.gleif or args.reset_staging):
        ap.error("discovery cannot combine with promotion, GLEIF or reset")
    if args.legacy_snapshot and any(name in os.environ for name in ("CI", "GITHUB_ACTIONS")):
        ap.error("--legacy-snapshot is private data: forbidden in CI")

    results = {}
    if args.store == "supabase":
        from collectors.companies.pg_store import PgStore
        store = PgStore()
        if args.reset_staging:
            if not args.migrate:
                ap.error("--reset-staging requires --migrate")
            results["reset"] = store.reset_staging()
        if args.migrate:
            results["migration"] = store.migrate(
                approved_evidence_003_sha256=args.approve_evidence_migration_003_sha256)
        elif not store.schema_exists():
            ap.error("schema companies is absent: run with --migrate")
    else:
        if not args.db and not args.dry_run:
            ap.error("--db required unless --dry-run")
        if args.reset_staging:
            ap.error("--reset-staging is for the Supabase staging schema only")
        store = SqliteStore(':memory:' if args.dry_run else args.db)
    if args.snapshot_file:
        class VerifiedSnapshotHttp:
            """Offline stand-in for the existing HTTP port; does not make network requests."""
            def get_bytes(self, url, accept="*/*"):
                if url != eurosatory.URL:
                    raise ValueError("snapshot bound to Eurosatory official URL only")
                return raw
        http = VerifiedSnapshotHttp()
    else:
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
        adapter = eurosatory.Exhibitors2024(raw_dir=args.raw_dir) if name == "eurosatory2024" else ADAPTERS[name]()
        results[name] = pipeline.run_source(store, adapter, http, args.limit)
    if args.gleif:
        results["gleif"] = pipeline.enrich_identities(store, gleif.Gleif(http), args.gleif)
    if args.promote:
        results["cards"] = cards.promote(store)
    for path in args.benchmark:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        entries = data["entries"] if isinstance(data, dict) else data
        results["completeness:" + pathlib.Path(path).stem] = report.completeness(store, entries)
    if args.report:
        results["summary"] = report.summary(store)
    if args.sample:
        results["sample"] = report.sample(store, args.sample)
    print(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    return 1 if any(isinstance(r, dict) and r.get("status") == "error" for r in results.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
