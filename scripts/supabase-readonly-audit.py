#!/usr/bin/env python3
"""Read-only, content-free audit of the live Supabase intelligence schema.

The report intentionally contains metadata and coarse status counts only. It never
selects record bodies, titles, URLs, coordinates, people, or other row content.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

OUTPUT_DIR = Path("output/supabase-readonly-audit")
REPORT_PATH = OUTPUT_DIR / "report.json"
SCHEMA = "intelligence"
STATUS_COLUMNS = {"status", "qa_status", "qa_decision", "decision", "publication_status"}
CONTRACT_ALIASES = {
    "headline": ("headline", "title"),
    "published_at": ("published_at", "publication_date", "published_date"),
    "country_iso2": ("country_iso2", "country_code"),
    "event_id": ("event_id", "id"),
    "company_id": ("company_id", "entity_id"),
    "takeaway": ("takeaway", "summary"),
    "source_url": ("source_url", "url"),
    "content_hash": ("content_hash", "source_hash", "sha256"),
    "qa.decision": ("qa_decision", "decision", "qa_status", "status"),
    "qa.public_release": ("public_release", "is_public", "published"),
}

METADATA_QUERIES = {
    "columns": """
        SELECT table_name, column_name, data_type, is_nullable, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = %s
        ORDER BY table_name, ordinal_position
    """,
    "relations": """
        SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm')
        ORDER BY c.relname
    """,
    "views": """
        SELECT viewname, definition
        FROM pg_catalog.pg_views
        WHERE schemaname = %s
        ORDER BY viewname
    """,
    "grants": """
        SELECT table_name, grantee, privilege_type, is_grantable
        FROM information_schema.role_table_grants
        WHERE table_schema = %s
        ORDER BY table_name, privilege_type, grantee
    """,
}


def write_report(report: dict) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def blocked_report(reason: str) -> dict:
    return {
        "status": "BLOCKED",
        "reason": reason,
        "mode": "READ_ONLY",
        "schema": SCHEMA,
        "record_content_included": False,
        "operator_sql": "docs/sql/supabase-readonly-audit.sql",
    }


def sanitize_view_definition(definition: str) -> dict:
    # Preserve useful structure while preventing string literals/comments from
    # leaking data embedded in a view definition into a public CI artifact.
    without_comments = re.sub(r"--[^\n]*|/\*.*?\*/", "<comment>", definition, flags=re.S)
    sanitized = re.sub(r"'(?:''|[^'])*'", "'<literal>'", without_comments)
    return {
        "sanitized_definition": " ".join(sanitized.split())[:4000],
        "definition_sha256": hashlib.sha256(definition.encode()).hexdigest(),
    }


def safe_grantee(grantee: str) -> str:
    if grantee in {"PUBLIC", "anon", "authenticated", "service_role", "postgres"}:
        return grantee
    return "role-sha256:" + hashlib.sha256(grantee.encode()).hexdigest()[:12]


def contract_map(columns: list[dict]) -> dict:
    locations: dict[str, list[str]] = {}
    for row in columns:
        locations.setdefault(row["column_name"], []).append(row["table_name"])
    return {
        field: [
            {"column": alias, "relations": sorted(locations[alias])}
            for alias in aliases
            if alias in locations
        ]
        for field, aliases in CONTRACT_ALIASES.items()
    }


def audit() -> dict:
    password = os.getenv("SUPABASE_DB_PASSWORD")
    if not password:
        return blocked_report("SUPABASE_DB_PASSWORD is not available")

    import psycopg
    from psycopg import sql

    report = {
        "status": "PASS",
        "audited_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "mode": "READ_ONLY",
        "schema": SCHEMA,
        "record_content_included": False,
        "operator_sql": "docs/sql/supabase-readonly-audit.sql",
    }
    try:
        with psycopg.connect(
            host=os.getenv("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
            port=int(os.getenv("SUPABASE_DB_PORT", "5432")),
            user=os.getenv("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
            dbname=os.getenv("SUPABASE_DB_NAME", "postgres"),
            password=password,
            sslmode="require",
            connect_timeout=20,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("BEGIN READ ONLY")
                cursor.execute("SET LOCAL statement_timeout = '15000ms'")
                cursor.execute("SET LOCAL lock_timeout = '2000ms'")
                cursor.execute("SET LOCAL idle_in_transaction_session_timeout = '20000ms'")

                cursor.execute(METADATA_QUERIES["columns"], (SCHEMA,))
                columns = [
                    dict(zip(("table_name", "column_name", "data_type", "is_nullable", "ordinal_position"), row))
                    for row in cursor.fetchall()
                ]
                cursor.execute(METADATA_QUERIES["relations"], (SCHEMA,))
                relations = [
                    dict(zip(("name", "kind", "rls_enabled", "rls_forced"), row))
                    for row in cursor.fetchall()
                ]
                cursor.execute(METADATA_QUERIES["views"], (SCHEMA,))
                views = [
                    {"name": name, **sanitize_view_definition(definition)}
                    for name, definition in cursor.fetchall()
                ]
                cursor.execute(METADATA_QUERIES["grants"], (SCHEMA,))
                grants = [
                    {
                        "relation": relation,
                        "grantee": safe_grantee(grantee),
                        "privilege": privilege,
                        "grantable": grantable,
                    }
                    for relation, grantee, privilege, grantable in cursor.fetchall()
                ]

                relation_kinds = {row["name"]: row["kind"] for row in relations}
                status_counts = []
                for column in columns:
                    if column["column_name"] not in STATUS_COLUMNS:
                        continue
                    relation = column["table_name"]
                    if relation_kinds.get(relation) not in {"r", "p", "v", "m"}:
                        continue
                    query = sql.SQL("""
                        SELECT CASE
                            WHEN {field} IS NULL THEN 'NULL'
                            WHEN upper({field}::text) IN ('APPROVE', 'APPROVED') THEN 'APPROVE'
                            WHEN upper({field}::text) = 'HOLD' THEN 'HOLD'
                            WHEN upper({field}::text) IN ('REJECT', 'REJECTED') THEN 'REJECT'
                            WHEN upper({field}::text) IN ('PUBLISH', 'PUBLISHED') THEN 'PUBLISHED'
                            ELSE 'OTHER'
                        END AS bucket, count(*)::bigint
                        FROM {schema}.{relation}
                        GROUP BY 1 ORDER BY 1
                    """).format(
                        field=sql.Identifier(column["column_name"]),
                        schema=sql.Identifier(SCHEMA),
                        relation=sql.Identifier(relation),
                    )
                    cursor.execute(query)
                    status_counts.append({
                        "relation": relation,
                        "column": column["column_name"],
                        "counts": {bucket: count for bucket, count in cursor.fetchall()},
                    })

                report.update({
                    "columns": columns,
                    "relations": relations,
                    "views": views,
                    "grants": grants,
                    "status_counts": status_counts,
                    "pr53_contract_candidates": contract_map(columns),
                })
                connection.rollback()
    except Exception as error:
        report = blocked_report(type(error).__name__)
    return report


def main() -> int:
    report = audit()
    write_report(report)
    print(f"Supabase schema audit: {report['status']} (content-free, read-only)")
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
