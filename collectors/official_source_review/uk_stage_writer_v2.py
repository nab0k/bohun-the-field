"""EXP-009 stage 1: UK FTS staging writer with tender history (schema after M1).

One notice per ocid, one append-only version per release (D1/D2). Manual only;
no schedule. Dry run by default; same guards as R7.1 (UK-local window, minimal
privilege preflight, one transaction, advisory lock). Never touches
intelligence.items or checkpoints.
"""
from __future__ import annotations

import json
import os

import uk_fields
from uk_staging_writer import (
    LOCK_ID, StagingError, _DryRun, _approved, _preflight, source_window_utc,
)

HOLD_METADATA = {"bohun": {"qa": "HOLD", "verification_status": "UNVERIFIED",
                           "public_release": False, "ready_for_import": False}}
HAS_TENDER, UPDATE_ONLY = "HAS_TENDER_NOTICE", "UPDATE_ONLY"

FIND_NOTICE = """SELECT id, latest_release_date, completeness FROM bohun_uk_intake.notices
WHERE ocid=%s FOR UPDATE"""
FIND_VERSIONS = """SELECT source_record_sha256 FROM bohun_uk_intake.notice_versions
WHERE ocid=%s AND release_id=%s"""
SEEN = "UPDATE bohun_uk_intake.notices SET last_seen_at=now() WHERE id=%s"
MARK_TENDER = """UPDATE bohun_uk_intake.notices SET completeness='HAS_TENDER_NOTICE',
last_seen_at=now() WHERE id=%s"""
INSERT_VERSION = """INSERT INTO bohun_uk_intake.notice_versions
(notice_id,ocid,release_id,release_tag,release_date,source_record_sha256,release_json,
package_license,package_publisher,applied_to_current,reason)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""
RECEIPT = """INSERT INTO bohun_uk_intake.run_receipts
(window_start,window_end,started_at,completed_at,status,source_window_complete,
observed_releases,tender_candidates,inserted_count,updated_count,repeated_count,
releases_seen_tender,releases_seen_update,versions_inserted)
VALUES (%s,%s,now(),now(),'COMPLETE',true,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id"""


def insert_notice_sql(columns):
    """Columns come only from the fixed whitelist, never from source data."""
    fixed = ["external_id", "ocid", "latest_release_id", "latest_release_date",
             "latest_source_sha256", "completeness", "metadata"]
    if not set(columns) <= set(uk_fields.FIELD_COLUMNS):
        raise StagingError("unknown_column")
    cols = fixed + list(columns)
    return ("INSERT INTO bohun_uk_intake.notices (" + ",".join(cols) + ") VALUES ("
            + ",".join(["%s"] * len(cols)) + ") RETURNING id")


def update_notice_sql(columns):
    if not set(columns) <= set(uk_fields.FIELD_COLUMNS):
        raise StagingError("unknown_column")
    sets = ["latest_release_id=%s", "latest_release_date=%s", "latest_source_sha256=%s",
            "completeness=%s"] + [c + "=%s" for c in columns]
    return ("UPDATE bohun_uk_intake.notices SET " + ",".join(sets)
            + ",last_seen_at=now(),last_changed_at=now() WHERE id=%s")


def validate_window(window, *, window_start, window_end):
    if not isinstance(window, dict) or (window.get("source") != "uk_fts"
            or window.get("qa") != "HOLD" or window.get("public_release") is not False
            or window.get("source_window_complete") is not True):
        raise StagingError("no_verified_hold_window")
    if window_start.tzinfo is None or window_end.tzinfo is None or window_start >= window_end:
        raise StagingError("invalid_window_bounds")
    if (window_end - window_start).total_seconds() > 3600:
        raise StagingError("pilot_window_too_long")
    releases = window.get("releases")
    if not isinstance(releases, list):
        raise StagingError("releases_missing")
    keys = [(r.ocid, r.release_id) for r in releases]
    if len(set(keys)) != len(keys):
        raise StagingError("duplicate_window_identity")
    if [(r.release_date, r.release_id) for r in releases] != sorted(
            (r.release_date, r.release_id) for r in releases):
        raise StagingError("releases_not_ordered")
    for r in releases:
        if not window_start <= r.release_date <= window_end:
            raise StagingError("candidate_outside_receipt_window")
        if r.tag not in uk_fields.TAGS:
            raise StagingError("unexpected_release_tag")
    return releases


def _apply_cursor(cur, window, window_start, window_end, *, adapter):
    """Caller owns the transaction. Order of releases is the order of application."""
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
    counts = {"inserted": 0, "updated": 0, "repeated": 0, "late": 0, "versions": 0}
    for r in window["releases"]:
        cur.execute(FIND_NOTICE, (r.ocid,))
        notice = cur.fetchone()
        cur.execute(FIND_VERSIONS, (r.ocid, r.release_id))
        known = {row[0] for row in cur.fetchall()}
        if notice is not None and r.sha256 in known:
            cur.execute(SEEN, (notice[0],))
            counts["repeated"] += 1
            continue
        if notice is None and known:
            raise StagingError("version_without_notice")
        reason = "SOURCE_REPUBLISHED" if known else "NEW_RELEASE"
        columns = [c for c in uk_fields.FIELD_COLUMNS if c in r.fields]
        values = [r.fields[c] for c in columns]
        if notice is None:
            if "title" not in r.fields:
                raise StagingError("title_missing_for_new_notice")
            completeness = HAS_TENDER if r.tag == "tender" else UPDATE_ONLY
            cur.execute(insert_notice_sql(columns), [
                r.ocid, r.ocid, r.release_id, r.release_date, r.sha256, completeness,
                adapter(HOLD_METADATA, json=True)] + values)
            row = cur.fetchone()
            if not row:
                raise StagingError("insert_id_missing")
            notice_id, applied = row[0], True
            counts["inserted"] += 1
        else:
            notice_id, latest_date, old_completeness = notice
            completeness = HAS_TENDER if (r.tag == "tender" or old_completeness == HAS_TENDER) else UPDATE_ONLY
            applied = latest_date is None or r.release_date >= latest_date
            if applied:
                cur.execute(update_notice_sql(columns), [
                    r.release_id, r.release_date, r.sha256, completeness] + values + [notice_id])
                counts["updated"] += 1
            else:
                cur.execute(MARK_TENDER if completeness != old_completeness else SEEN, (notice_id,))
                counts["late"] += 1
        cur.execute(INSERT_VERSION, (
            notice_id, r.ocid, r.release_id, r.tag, r.release_date, r.sha256,
            adapter(r.clean, json=True), window["license"], window["publisher"], applied, reason))
        counts["versions"] += 1
    tags = [r.tag for r in window["releases"]]
    cur.execute(RECEIPT, (window_start, window_end, window["observations"], len(tags),
                          counts["inserted"], counts["updated"], counts["repeated"],
                          tags.count("tender"), tags.count("tenderUpdate"), counts["versions"]))
    row = cur.fetchone()
    if not row:
        raise StagingError("receipt_missing")
    return {"qa": "HOLD", "public_release": False, "releases": len(tags),
            "counts": counts, "receipt_id": str(row[0]), "checkpoint_advanced": False}


def _psycopg_adapter():
    from psycopg.types.json import Jsonb

    def adapt(value, json=False):
        return Jsonb(value) if json else value
    return adapt


def write_window(window, *, window_start, window_end, connect=None, dry_run=True):
    if type(dry_run) is not bool:
        raise StagingError("dry_run_must_be_bool")
    validate_window(window, window_start=window_start, window_end=window_end)
    dsn = _approved()
    if connect is None:
        import psycopg
        connect, adapter = psycopg.connect, _psycopg_adapter()
    else:
        adapter = lambda value, json=False: value  # fake database tests only
    with connect(dsn, sslmode="require", connect_timeout=15) as conn:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT current_user")
                    who = cur.fetchone()
                    allowed = os.environ.get("BOHUN_UK_DB_WRITER_LOGIN", "")
                    if (not who or who[0] != allowed or allowed in
                            {"postgres", "supabase_admin", "service_role", "anon", "authenticated", ""}):
                        raise StagingError("wrong_db_writer_identity")
                    _preflight(cur)
                    cur.execute("SET LOCAL statement_timeout = '60s'")
                    result = _apply_cursor(cur, window, window_start, window_end, adapter=adapter)
                    if dry_run:
                        raise _DryRun(result)
                    return {**result, "dry_run": False, "committed": True}
        except _DryRun as rolled_back:
            return {**rolled_back.result, "dry_run": True, "committed": False}


def write_live_window(start, end, *, dry_run=True, connect=None, transport=None):
    from uk_window import prepare_live_window
    window_start, window_end = source_window_utc(start, end)
    window = prepare_live_window(start, end, transport=transport)
    return write_window(window, window_start=window_start, window_end=window_end,
                        connect=connect, dry_run=dry_run)


def main():
    import argparse
    p = argparse.ArgumentParser(description="Manual UK FTS stage-1 staging run (default: dry run, rolled back)")
    p.add_argument("--from", dest="start", required=True, help="UK local, e.g. 2026-10-15T09:00:00")
    p.add_argument("--to", dest="end", required=True)
    p.add_argument("--commit", action="store_true", help="Commit instead of rolling back")
    a = p.parse_args()
    print(json.dumps(write_live_window(a.start, a.end, dry_run=not a.commit), sort_keys=True))


if __name__ == "__main__":
    main()
