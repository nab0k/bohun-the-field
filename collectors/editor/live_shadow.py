"""Read-only live C3-10 shadow adapter for freshly generated synthesis versions.

This is not a release gate. No source retrieval, model calls, publisher connection,
state writes or trusted assessments. In particular a stored admission PUBLISH is
never accepted as evidence of independent editorial verification.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path

from .shadow import MAX_ROWS, shadow_review

MAX_LOOKBACK_HOURS = 168
DEFAULT_LOOKBACK_HOURS = 24
DEFAULT_LIMIT = 30

# A latest-version material view, deliberately not restricted to published rows.
# All SQL is fixed SELECT text. Parameter values cannot inject SQL.
LATEST_VERSIONS_SQL = """
WITH latest AS (
    SELECT DISTINCT ON (s.id)
        s.id, s.event_id, s.is_hidden, s.language,
        v.version, v.generator, v.created_at,
        v.headline, v.what_happened, v.context,
        v.policy_direction, v.bigger_picture, v.supply_chain,
        v.why_it_matters, v.sources, v.context_sources
    FROM knowledge.synthesis AS s
    JOIN knowledge.synthesis_version AS v ON v.synthesis_id = s.id
    ORDER BY s.id, v.version DESC
)
SELECT
    id, event_id, version, generator,
    created_at AS published_at, language,
    headline, what_happened, context, policy_direction,
    bigger_picture, supply_chain, why_it_matters,
    sources, context_sources
FROM latest
WHERE is_hidden IS FALSE
  AND created_at >= now() - (%s::integer * interval '1 hour')
ORDER BY created_at DESC, id DESC
LIMIT %s::integer
"""


def _positive_bounded(value: int, *, upper: int, what: str) -> int:
    if type(value) is not int or not 1 <= value <= upper:
        raise ValueError(f"invalid_{what}")
    return value


def _format_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def query_latest(conn, *, lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
                 limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Enforce server-side READ ONLY BEFORE any data query and load <=30 rows."""
    _positive_bounded(lookback_hours, upper=MAX_LOOKBACK_HOURS, what="lookback_hours")
    _positive_bounded(limit, upper=MAX_ROWS, what="limit")
    conn.execute("SET TRANSACTION READ ONLY")
    mode = conn.execute("SHOW transaction_read_only").fetchone()
    if not mode or str(mode[0]).lower() != "on":
        raise RuntimeError("database_read_only_unconfirmed")
    with conn.cursor() as cur:
        cur.execute(LATEST_VERSIONS_SQL, (lookback_hours, limit))
        names = [col.name for col in cur.description]
        results = []
        for raw in cur.fetchall():
            if len(raw) != len(names):
                raise ValueError("database_row_shape_mismatch")
            results.append({name: _format_value(value)
                            for name, value in zip(names, raw)})
        if len(results) > limit:
            raise ValueError("database_row_limit_exceeded")
    return results


def run_shadow(conn, *, lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
               limit: int = DEFAULT_LIMIT) -> dict:
    """Return IDs, codes and counts ONLY. Never publish or authorize a PASS."""
    rows = query_latest(conn, lookback_hours=lookback_hours, limit=limit)
    if not rows:
        result = {
            "schema": "c3-10-shadow-report-v1", "mode": "read-only-offline-no-model",
            "count": 0, "decisions": {"PASS": 0, "REVIEW": 0, "BLOCK": 0},
            "publication_authorized": False, "results": [],
        }
    else:
        result = shadow_review(rows)
    if result["decisions"]["PASS"] or result.get("publication_authorized") is not False:
        raise AssertionError("shadow_must_never_authorize_publication")
    return {
        **result,
        "schema": "c3-10-live-shadow-report-v1",
        "mode": "live-db-read-only-structural-shadow-no-model",
        "selection": "latest_nonhidden_synthesis_versions",
        "lookback_hours": lookback_hours, "limit": limit,
        "independent_fact_check_performed": False,
        "database_writes": 0, "model_calls": 0,
        "publication_authorized": False, "offline_only": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback-hours", type=int, default=DEFAULT_LOOKBACK_HOURS)
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    _positive_bounded(args.lookback_hours, upper=MAX_LOOKBACK_HOURS, what="lookback_hours")
    _positive_bounded(args.limit, upper=MAX_ROWS, what="limit")
    # Reuse the existing pinned-CA database connector, NOT the synthesis publisher.
    from collectors.synthesis.live import connect
    conn = connect()
    try:
        result = run_shadow(conn, lookback_hours=args.lookback_hours, limit=args.limit)
    finally:
        # No commit is allowed, even for a fully read-only transaction.
        try:
            conn.rollback()
        finally:
            conn.close()
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print("EDITOR_SHADOW_RESULT " + json.dumps({
        "count": result["count"], "decisions": result["decisions"],
        "publication_authorized": False, "model_calls": 0,
        "database_writes": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
