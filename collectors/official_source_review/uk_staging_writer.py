"""EXP-009 R7: private UK Find a Tender HOLD metadata writer.

REVIEW ONLY. Not on a cron, not auto-invoked and not a substitute for approval.
Uses real R6 `prepare_bounded_live` *in memory*; never writes to intelligence.items.
Required: a private PostgreSQL credential, bounded source receipt, approved pilot.

NOTICE: This module intentionally cannot fetch source data on its own and requires
operator-controlled invocation after confirming licensing/provenance.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
import re
from urllib.parse import urlsplit

SCHEMA = "bohun_uk_intake"
LOCK_ID = 6846691490514231850
SOURCE = "uk_fts"
SOURCE_API = "https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages"
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MAX_TENDERS = 10
# Observed 2026-10-22 (EXP-009 R7 check): updatedFrom/updatedTo without offset
# select releases dated in UK local time (+01:00 in BST), not UTC.
SOURCE_TZ = "Europe/London"


class StagingError(ValueError):
    pass


def _approved():
    a = os.environ.get("BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF", "")
    if not a.startswith("owner-approved:"):
        raise StagingError("explicit_owner_approval_missing")
    return _dsn_without_secret()


def _canonical_metadata(c):
    """Keep exclusively R6 sanitized provenance; reject arbitrary source bodies."""
    external_id, title, digest = c.external_id, c.title, c.record_sha256
    data = c.raw_data
    if not isinstance(external_id, str) or not 1 <= len(external_id) <= 512:
        raise StagingError("invalid_external_id")
    if not isinstance(title, str) or not title.strip() or len(title) > 2000:
        raise StagingError("invalid_title")
    if not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
        raise StagingError("invalid_sha256")
    if getattr(c, "item_type", None) != "tender" or getattr(c, "url", None) is not None:
        raise StagingError("unsafe_notice_type_or_url")
    if not isinstance(data, dict) or set(data) != {"bohun", "uk_fts"}:
        raise StagingError("unsanitized_metadata")
    b, uk = data.get("bohun"), data.get("uk_fts")
    if not isinstance(b, dict) or not isinstance(uk, dict):
        raise StagingError("invalid_provenance")
    if set(b) != {"qa", "verification_status", "public_release", "capture_status", "ready_for_import", "current_procurement_status"}:
        raise StagingError("invalid_bohun_fields")
    if b != {"qa": "HOLD", "verification_status": "UNVERIFIED", "public_release": False,
             "capture_status": "METADATA_ONLY", "ready_for_import": False,
             "current_procurement_status": "unknown"}:
        raise StagingError("publication_gate_failed")
    if set(uk) != {"ocid", "release_id", "source_api", "tags", "source_record_sha256", "release_date_raw"}:
        raise StagingError("invalid_uk_fields")
    ocid, rid, tags = uk.get("ocid"), uk.get("release_id"), uk.get("tags")
    if not isinstance(ocid, str) or not isinstance(rid, str):
        raise StagingError("identity_not_string")
    if not ocid or not rid or len(ocid) > 512 or len(rid) > 512:
        raise StagingError("invalid_origin_identity")
    if external_id != ocid + "::" + rid:
        raise StagingError("identity_mismatch")
    if uk.get("source_api") != SOURCE_API or uk.get("source_record_sha256") != digest:
        raise StagingError("origin_or_hash_mismatch")
    if not isinstance(tags, list) or not all(isinstance(x, str) for x in tags) or "tender" not in tags:
        raise StagingError("not_tender_tagged")
    if tags != sorted(set(tags)) or len(tags) > 30:
        raise StagingError("noncanonical_tags")
    if not (uk["release_date_raw"] is None or isinstance(uk["release_date_raw"], str)):
        raise StagingError("invalid_release_date_raw")
    if not (c.published_at is None or (isinstance(c.published_at, datetime) and c.published_at.tzinfo is not None)):
        raise StagingError("invalid_published_at")
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > 16384:
        raise StagingError("metadata_limit")
    return (external_id, ocid, rid, title, c.published_at, digest, tags, data)


def source_window_utc(start, end):
    """Naive uk_probe window strings -> UTC bounds as the source interprets them.

    Refuses windows touching a DST change: ambiguous local hours cannot be
    stored as a reliable receipt or future checkpoint.
    """
    from zoneinfo import ZoneInfo
    try:
        a, b = [datetime.strptime(v, "%Y-%m-%dT%H:%M:%S") for v in (start, end)]
    except (TypeError, ValueError):
        raise StagingError("bad_source_window") from None
    tz = ZoneInfo(SOURCE_TZ)
    bounds = []
    for v in (a, b):
        early, late = v.replace(tzinfo=tz, fold=0), v.replace(tzinfo=tz, fold=1)
        if early.utcoffset() != late.utcoffset():
            raise StagingError("source_window_dst_ambiguous")
        u = early.astimezone(timezone.utc)
        if u.astimezone(tz).replace(tzinfo=None) != v:
            raise StagingError("source_window_dst_nonexistent")
        bounds.append(u)
    if bounds[1] - bounds[0] != b - a:
        raise StagingError("source_window_dst_crossing")
    return bounds[0], bounds[1]


def validate_manifest(manifest, *, window_start, window_end):
    if not isinstance(manifest, dict) or (manifest.get("source") != SOURCE
           or manifest.get("qa") != "HOLD" or manifest.get("public_release") is not False
           or manifest.get("source_window_complete") is not True
           or manifest.get("production_write_approved") is not False):
        raise StagingError("no_verified_hold_manifest")
    if not isinstance(window_start, datetime) or not isinstance(window_end, datetime):
        raise StagingError("missing_window_bounds")
    if window_start.tzinfo is None or window_end.tzinfo is None or window_start >= window_end:
        raise StagingError("invalid_window_bounds")
    if (window_end - window_start).total_seconds() > 3600:
        raise StagingError("pilot_window_too_long")
    candidates = manifest.get("candidates")
    if not isinstance(candidates, list) or not (1 <= len(candidates) <= MAX_TENDERS):
        raise StagingError("pilot_tender_count_outside_1_to_10")
    if manifest.get("pilot_candidates") != len(candidates):
        raise StagingError("candidate_count_mismatch")
    if manifest.get("selection_limited") is True:
        raise StagingError("window_has_more_than_10_tenders")
    if manifest.get("eligible_unique_tender_notices") != len(candidates):
        raise StagingError("window_not_fully_selected")
    if type(manifest.get("observations")) is not int or manifest["observations"] < len(candidates):
        raise StagingError("observation_count_invalid")
    clean = [_canonical_metadata(c) for c in candidates]
    # Freeze and verify the *updatedFrom/updatedTo request bounds*, not release.date.
    # The R8 manifest comes from the same in-memory complete source read.
    from r8_bridge import require_bound_receipt, BridgeError
    try:
        require_bound_receipt(manifest, window_start=window_start, window_end=window_end)
    except BridgeError as exc:
        raise StagingError(str(exc)) from None
    ext = [r[0] for r in clean]
    origin = [(r[1],r[2]) for r in clean]
    if len(set(ext)) != len(ext) or len(set(origin)) != len(origin):
        raise StagingError("duplicate_window_identity")
    return clean


FIND = """SELECT id,ocid,release_id,source_record_sha256 FROM bohun_uk_intake.notices
WHERE external_id=%s FOR UPDATE"""
INSERT = """INSERT INTO bohun_uk_intake.notices
(external_id,ocid,release_id,title,published_at,notice_url,source_record_sha256,tags,metadata)
VALUES (%s,%s,%s,%s,%s,NULL,%s,%s,%s) RETURNING id"""
SEEN = "UPDATE bohun_uk_intake.notices SET last_seen_at=now() WHERE id=%s"
UPDATE = """UPDATE bohun_uk_intake.notices SET title=%s,published_at=%s,
source_record_sha256=%s,tags=%s,metadata=%s,last_seen_at=now(),last_changed_at=now()
WHERE id=%s"""
VERSION = """INSERT INTO bohun_uk_intake.notice_versions
(notice_id,old_record_sha256,new_record_sha256) VALUES (%s,%s,%s)"""
# Fail closed unless the session is the narrow writer and nothing broader.
# Writer INSERT rights are column-level, so has_any_column_privilege, not
# has_table_privilege (which ignores column grants).
# intelligence.items is looked up via pg_class: to_regclass() raises
# "permission denied for schema" for a role without USAGE on intelligence.
PREFLIGHT = """SELECT r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb,
 coalesce(has_table_privilege(current_user,
   (SELECT c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'intelligence' AND c.relname = 'items'),
   'SELECT,INSERT,UPDATE,DELETE'), false),
 has_table_privilege(current_user, 'bohun_uk_intake.checkpoints', 'SELECT,INSERT,UPDATE,DELETE'),
 CASE WHEN to_regnamespace('exp009') IS NULL THEN false
      ELSE has_schema_privilege(current_user, 'exp009', 'USAGE') END,
 has_any_column_privilege(current_user, 'bohun_uk_intake.notices', 'INSERT'),
 pg_has_role(current_user, 'bohun_uk_ingest_writer', 'USAGE')
FROM pg_roles r WHERE r.rolname = current_user"""
RECEIPT = """INSERT INTO bohun_uk_intake.run_receipts
(window_start,window_end,started_at,completed_at,status,source_window_complete,
observed_releases,tender_candidates,inserted_count,updated_count,repeated_count)
VALUES (%s,%s,now(),now(),'COMPLETE',true,%s,%s,%s,%s,%s) RETURNING id"""


def _apply_cursor(cur, clean, window_start, window_end, observations, *, adapter):
    """Execute with a transactional SQL cursor. Caller owns transaction/rollback."""
    cur.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
    counts = {"inserted": 0, "updated": 0, "repeated": 0}
    for x in clean:
        external_id, ocid, rid, title, at, digest, tags, metadata = x
        cur.execute(FIND, (external_id,))
        existing = cur.fetchone()
        if existing is None:
            cur.execute(INSERT, (external_id, ocid, rid, title, at, digest, tags, adapter(metadata)))
            if not cur.fetchone():
                raise StagingError("insert_id_missing")
            counts["inserted"] += 1
        else:
            item_id, old_ocid, old_rid, old_hash = existing
            if (old_ocid, old_rid) != (ocid, rid):
                raise StagingError("source_identity_collision")
            if not isinstance(old_hash, str) or not HASH_RE.fullmatch(old_hash):
                raise StagingError("existing_hash_invalid")
            if old_hash == digest:
                cur.execute(SEEN, (item_id,))
                counts["repeated"] += 1
            else:
                cur.execute(UPDATE, (title, at, digest, tags, adapter(metadata), item_id))
                cur.execute(VERSION, (item_id, old_hash, digest))
                counts["updated"] += 1
    cur.execute(RECEIPT, (window_start, window_end, observations, len(clean),
                          counts["inserted"], counts["updated"], counts["repeated"]))
    row = cur.fetchone()
    if not row:
        raise StagingError("receipt_missing")
    # Deliberately do NOT advance checkpoints during first historical ≤10 pilot.
    return {"qa": "HOLD", "public_release": False, "candidate_count": len(clean),
            "counts": counts, "receipt_id": str(row[0]), "checkpoint_advanced": False}


class _DryRun(Exception):
    def __init__(self, result):
        self.result = result


def _preflight(cur):
    cur.execute(PREFLIGHT)
    row = cur.fetchone()
    if not row or len(row) != 9:
        raise StagingError("writer_preflight_unavailable")
    names = ("superuser", "bypassrls", "createrole", "createdb", "intelligence_items",
             "checkpoints", "exp009", "notices_insert_missing", "writer_membership_missing")
    failed = [n for n, v in zip(names, row[:7]) if v is not False]
    failed += [n for n, v in zip(names[7:], row[7:]) if v is not True]
    if failed:  # check names only, no data
        raise StagingError("writer_privileges_not_minimal:" + ",".join(failed))


def write_reviewed_pilot(manifest, *, window_start, window_end, connect=None, dry_run=True):
    """Manual-only entrypoint; never accept untrusted on-disk JSON as a manifest.

    dry_run (default) executes every statement under the real role, grants, RLS
    and constraints, then rolls the whole transaction back.
    """
    if type(dry_run) is not bool:
        raise StagingError("dry_run_must_be_bool")
    clean = validate_manifest(manifest, window_start=window_start, window_end=window_end)
    dsn = _approved()
    if connect is None:
        import psycopg
        from psycopg.types.json import Jsonb
        connect = psycopg.connect
        adapter = Jsonb
    else:
        adapter = lambda data: data  # Fake database tests only
    with connect(dsn, sslmode="require", connect_timeout=15) as conn:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SELECT current_user")
                    who = cur.fetchone()
                    allowed_login = os.environ.get("BOHUN_UK_DB_WRITER_LOGIN", "")
                    if (not who or who[0] != allowed_login or allowed_login in
                            {"postgres", "supabase_admin", "service_role", "anon", "authenticated", ""}):
                        raise StagingError("wrong_db_writer_identity")
                    _preflight(cur)
                    cur.execute("SET LOCAL statement_timeout = '60s'")
                    result = _apply_cursor(cur, clean, window_start, window_end,
                                           manifest["observations"], adapter=adapter)
                    if dry_run:
                        raise _DryRun(result)
                    return {**result, "dry_run": False, "committed": True}
        except _DryRun as rolled_back:
            return {**rolled_back.result, "dry_run": True, "committed": False}


def _dsn_without_secret():
    """Password comes from libpq PGPASSWORD (read from Keychain at launch), never the DSN."""
    dsn = os.environ.get("BOHUN_UK_STAGING_DSN", "")
    if not dsn:
        raise StagingError("private_postgres_dsn_missing")
    if "password" in dsn.lower() or re.search(r"://[^/@]*:[^/@]*@", dsn):
        raise StagingError("dsn_must_not_contain_password")
    return dsn


def check_connection(*, connect=None):
    """Step-3 check: log in, verify identity and minimal privileges, write nothing."""
    dsn = _dsn_without_secret()
    if connect is None:
        import psycopg
        connect = psycopg.connect
    with connect(dsn, sslmode="require", connect_timeout=15) as conn:
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute("SELECT current_user")
                    who = cur.fetchone()
                    if not who or who[0] != os.environ.get("BOHUN_UK_DB_WRITER_LOGIN", ""):
                        raise StagingError("wrong_db_writer_identity")
                    _preflight(cur)
                    raise _DryRun({"login": who[0], "privileges": "minimal", "writes": 0})
        except _DryRun as ok:
            return ok.result


def write_live_window(start, end, *, dry_run=True, connect=None, transport=None):
    """One bounded read of the official API, then one staging transaction."""
    # This is a *manual pilot* path. Complete source read + identity normalization
    # must succeed before attempting a DB connection. No checkpoints are advanced.
    from r8_source_reader import make_window, WindowError
    from r8_bridge import prepare_window, BridgeError
    from r8_transport import bounded_get
    from uk_hold_pilot import prepare_releases
    try:
        window = make_window(start, end)
        manifest = prepare_window(window, transport or bounded_get, prepare_releases)
    except (WindowError, BridgeError) as exc:
        raise StagingError(str(exc)) from None
    return write_reviewed_pilot(manifest, window_start=window.utc_from,
                                window_end=window.utc_to, connect=connect,
                                dry_run=dry_run)


def main():
    import argparse
    p = argparse.ArgumentParser(description="Manual UK FTS HOLD staging pilot (default: dry run, rolled back)")
    p.add_argument("--check-only", action="store_true", help="Login + privilege check only, no source read")
    p.add_argument("--from", dest="start", help="UK local, e.g. 2026-10-15T09:00:00")
    p.add_argument("--to", dest="end")
    p.add_argument("--commit", action="store_true", help="Commit instead of rolling back")
    a = p.parse_args()
    if a.check_only:
        print(json.dumps(check_connection(), sort_keys=True))
        return
    if not a.start or not a.end:
        p.error("--from and --to are required")
    result = write_live_window(a.start, a.end, dry_run=not a.commit)
    print(json.dumps(result, sort_keys=True))  # counts and receipt id only, no source data


if __name__ == "__main__":
    main()
