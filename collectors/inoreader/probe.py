#!/usr/bin/env python3
"""Bounded cloud acceptance probe for the existing INPUT-02 capture patch.

Preview still uses live OAuth/API and database reads. Run only offline tests until
the exact cloud run is approved. No model calls, backfill or publication approval.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

if __package__:
    from . import sync
    from .content_contract import CaptureBudget
elif __name__ == "__main__":
    import sync
    from content_contract import CaptureBudget
else:
    from collectors.inoreader import sync
    from collectors.inoreader.content_contract import CaptureBudget


BRANCH = "codex/exp009-cloud-pipeline"
MAX_FOLDERS = 3
PAGE_SIZE = 20
MAX_NEW = 5
SOURCE_NAME = "Inoreader BOHUN"
REPO = Path(__file__).resolve().parents[2]


class ProbeError(RuntimeError):
    """Only fixed diagnostic codes, never upstream exception text."""


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check_digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def verify_revision(expected_sha):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha or ""):
        raise ProbeError("EXPECTED_FULL_SHA_REQUIRED")
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=REPO,
                                       stderr=subprocess.DEVNULL).decode().strip()
    try:
        actual = git("rev-parse", "HEAD")
        branch = (os.environ.get("GITHUB_REF", "").removeprefix("refs/heads/")
                  if os.environ.get("GITHUB_ACTIONS") == "true"
                  else git("branch", "--show-current"))
        tracked_probe = git("ls-files", "--error-unmatch", "collectors/inoreader/probe.py")
        dirty = git("status", "--porcelain", "--untracked-files=no")
    except (OSError, subprocess.CalledProcessError):
        raise ProbeError("REVISION_CHECK_FAILED") from None
    if actual != expected_sha or branch != BRANCH or dirty or not tracked_probe:
        raise ProbeError("REVISION_OR_BRANCH_MISMATCH")


def collect(token):
    subscriptions = sync.api("subscription/list", token).get("subscriptions", [])
    folders, _ = sync.folders_from_subscriptions(subscriptions)
    if not folders:
        raise ProbeError("NO_BOHUN_FOLDERS")
    unique = {}
    fetched = 0
    chosen = folders[:MAX_FOLDERS]
    for folder in chosen:
        path = "stream/contents/" + urllib.parse.quote("user/-/label/" + folder, safe="/")
        response = sync.api(path, token, {"n": PAGE_SIZE})
        entries = response.get("items", [])
        if not isinstance(entries, list) or len(entries) > PAGE_SIZE:
            raise ProbeError("PROVIDER_PAGE_LIMIT_OR_SHAPE")
        fetched += len(entries)
        for item in entries:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
                raise ProbeError("PROVIDER_ITEM_SHAPE")
            unique.setdefault(item["id"], (item, folder))
        # Deliberately ignore continuation: no second page, no extra source.
    return unique, {"folders": len(chosen), "fetched": fetched, "unique": len(unique)}


def select_items(unique, known):
    def has_body(pair):
        summary = pair[0].get("summary")
        return (isinstance(summary, dict) and isinstance(summary.get("content"), str)
                and bool(summary["content"].strip()))
    new = [pair for key, pair in unique.items() if key not in known]
    new.sort(key=lambda pair: not has_body(pair))  # Stable: body-first, then encounter order.
    budget = CaptureBudget()
    selected = [sync.normalize(raw, folder, content_budget=budget)
                for raw, folder in new[:MAX_NEW]]
    return selected, budget.bytes_used


def batch_fingerprint(items):
    # Receipt timestamps differ between preview/apply; bind the actual provider
    # payload/provenance and extracted content, rather than the wall clock.
    result = []
    for item in items:
        raw = dict(item["raw_data"])
        capture = dict(raw["content_capture"])
        capture.pop("captured_at")
        raw["content_capture"] = capture
        result.append({**item, "raw_data": raw})
    return digest(result)


def validate_batch(items):
    if len(items) > MAX_NEW or len({item["external_id"] for item in items}) != len(items):
        raise ProbeError("WRITE_BATCH_LIMIT_OR_DUPLICATE")
    for item in items:
        capture = item["raw_data"]["content_capture"]
        if capture["completeness"] != "UNKNOWN":
            raise ProbeError("CAPTURE_COMPLETENESS_CHANGED")
        if any(field in item or field in item["raw_data"] or field in capture
               for field in ("public_release", "qa_status", "evidence_eligible")):
            raise ProbeError("PUBLICATION_FIELD_FORBIDDEN")


def validate_snapshot(snapshot):
    if not snapshot["rls"] or any(any(row[1:]) for row in snapshot["exposure"]):
        raise ProbeError("RAW_DATA_ACCESS_NOT_PRIVATE")
    if snapshot["triggers"]:
        raise ProbeError("UNREVIEWED_ENABLED_TRIGGER")
    if len(snapshot["wire"]) != 1:
        raise ProbeError("SOURCE_WIRE_MISSING_OR_AMBIGUOUS")


def verify_rows(items, actual):
    expected = {item["external_id"]: item for item in items}
    if actual != expected:
        raise ProbeError("STORED_BATCH_MISMATCH")


@contextmanager
def checked_transaction(store, *, readonly):
    finished = False
    try:
        with store.transaction(readonly=readonly):
            yield
            finished = True
    except Exception:
        # Losing the COMMIT acknowledgement does not establish a rollback.
        if finished and not readonly:
            raise ProbeError("COMMIT_UNCONFIRMED_NO_RETRY") from None
        raise


def run_probe(store, token, *, apply=False, expected_db=None, expected_batch=None):
    if apply and (not check_digest(expected_db) or not check_digest(expected_batch)):
        raise ProbeError("REVIEWED_DB_AND_BATCH_FINGERPRINTS_REQUIRED")
    unique, report = collect(token)
    with checked_transaction(store, readonly=not apply):
        snapshot = store.inspect()
        validate_snapshot(snapshot)
        source = store.source_id()
        fingerprint = digest({"catalog": snapshot, "source_id": str(source)})
        known = store.existing(source, list(unique))
        items, byte_count = select_items(unique, known)
        validate_batch(items)
        batch = batch_fingerprint(items)
        statuses = {}
        for item in items:
            status = item["raw_data"]["content_capture"]["status"]
            statuses[status] = statuses.get(status, 0) + 1
        report.update(mode="apply" if apply else "preview", selected=len(items),
                      capture_bytes=byte_count, statuses=statuses,
                      db_fingerprint=fingerprint, batch_fingerprint=batch,
                      inserted=0, conflicts=0, replay_inserted=0)
        if not apply:
            report["result"] = "PREVIEW_ONLY"
            return report
        if fingerprint != expected_db or batch != expected_batch:
            raise ProbeError("REVIEWED_FINGERPRINT_MISMATCH")
        # Recheck immediately before writes; an ordinary collector can have raced.
        known_now = store.existing(source, [item["external_id"] for item in items])
        writable = [item for item in items if item["external_id"] not in known_now]
        validate_batch(writable)
        inserted = []
        for item in writable:
            if store.insert(source, item):
                inserted.append(item)
        verify_rows(inserted, store.read(source, [item["external_id"] for item in inserted]))
        for item in inserted:
            if store.insert(source, item):
                raise ProbeError("REPLAY_CREATED_NEW_ROW")
        verify_rows(inserted, store.read(source, [item["external_id"] for item in inserted]))
        if digest({"catalog": store.inspect(), "source_id": str(store.source_id())}) != fingerprint:
            raise ProbeError("DB_CONTRACT_CHANGED")
        report.update(inserted=len(inserted), conflicts=len(items) - len(inserted))
    # The context above has committed. Never auto-retry if the new connection fails.
    try:
        with store.reopen() as confirmed:
            with confirmed.transaction(readonly=True):
                if digest({"catalog": confirmed.inspect(),
                           "source_id": str(confirmed.source_id())}) != fingerprint:
                    raise ProbeError("DB_CONTRACT_CHANGED")
                verify_rows(inserted, confirmed.read(source, [item["external_id"] for item in inserted]))
    except Exception:
        raise ProbeError("POST_COMMIT_UNCONFIRMED_NO_RETRY") from None
    bodies = sum(item["raw_data"]["content_capture"]["status"] ==
                 "BODY_PRESENT_COMPLETENESS_UNVERIFIED" for item in inserted)
    report["result"] = "VERIFIED_BODY_CAPTURE" if bodies else "INCONCLUSIVE_NO_NEW_BODY"
    return report


class PostgresStore:
    """Direct existing PostgreSQL connection, no schema changes or source creation."""

    def __init__(self, conn, factory):
        self.conn = conn
        self.factory = factory

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.conn.close()

    def transaction(self, *, readonly=False):
        @contextmanager
        def transaction():
            with self.conn.transaction():
                if readonly:
                    self.conn.execute("SET TRANSACTION READ ONLY")
                self.conn.execute("SET LOCAL statement_timeout = '20s'")
                self.conn.execute("SET LOCAL lock_timeout = '5s'")
                yield
        return transaction()

    def reopen(self):
        return self.factory()

    def source_id(self):
        rows = self.conn.execute("SELECT id FROM intelligence.sources WHERE name = %s",
                                 (SOURCE_NAME,)).fetchall()
        if len(rows) != 1:
            raise ProbeError("SOURCE_MISSING_OR_AMBIGUOUS")
        return rows[0][0]

    def existing(self, source, ids):
        if not ids:
            return set()
        return {row[0] for row in self.conn.execute(
            "SELECT external_id FROM intelligence.items WHERE source_id = %s AND external_id = ANY(%s)",
            (source, ids)).fetchall()}

    def insert(self, source, item):
        # The cap is also checked by run_probe immediately before this boundary.
        from psycopg.types.json import Jsonb
        row = self.conn.execute("""INSERT INTO intelligence.items
            (source_id, external_id, title, url, item_type, published_at, raw_data)
            VALUES (%s,%s,%s,%s,'news',%s,%s)
            ON CONFLICT (source_id, external_id) DO NOTHING RETURNING id""",
            (source, item["external_id"], item["title"], item["url"],
             item["published_at"], Jsonb(item["raw_data"]))).fetchone()
        return row is not None

    def read(self, source, ids):
        if not ids:
            return {}
        rows = self.conn.execute("""SELECT external_id, title, url, published_at, raw_data, item_type
            FROM intelligence.items WHERE source_id = %s AND external_id = ANY(%s)""",
            (source, ids)).fetchall()
        if any(row[5] != "news" for row in rows):
            raise ProbeError("STORED_ITEM_TYPE_MISMATCH")
        return {row[0]: dict(zip(("external_id", "title", "url", "published_at", "raw_data"), row[:5]))
                for row in rows}

    def inspect(self):
        # Catalog only: no execution of the source-wire function or raw item scan.
        rls = self.conn.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'intelligence.items'::regclass").fetchone()[0]
        exposure = self.conn.execute("""SELECT role,
            has_schema_privilege(role, 'intelligence', 'USAGE'),
            has_table_privilege(role, 'intelligence.items', 'SELECT'),
            has_column_privilege(role, 'intelligence.items', 'raw_data', 'SELECT')
            FROM (VALUES ('anon'), ('authenticated')) AS roles(role) ORDER BY role""").fetchall()
        columns = self.conn.execute("""SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns WHERE table_schema = 'intelligence'
            AND table_name = 'items' ORDER BY ordinal_position""").fetchall()
        triggers = self.conn.execute("""SELECT tgname, pg_get_triggerdef(oid)
            FROM pg_trigger WHERE tgrelid = 'intelligence.items'::regclass
            AND NOT tgisinternal AND tgenabled != 'D' ORDER BY tgname""").fetchall()
        wire = self.conn.execute("""SELECT pg_get_functiondef(p.oid), p.prosecdef,
            has_function_privilege('anon', p.oid, 'EXECUTE'),
            has_function_privilege('authenticated', p.oid, 'EXECUTE')
            FROM pg_proc p JOIN pg_namespace n ON p.pronamespace = n.oid
            WHERE n.nspname = 'public' AND p.proname = 'export_inoreader_source_wire'
            AND p.pronargs = 0 ORDER BY p.oid""").fetchall()
        return dict(rls=rls, exposure=exposure, columns=columns, triggers=triggers, wire=wire)


def open_store():
    import psycopg
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    if not password:
        raise ProbeError("MISSING_DB_PASSWORD")
    params = dict(host=os.environ.get("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
                  port=int(os.environ.get("SUPABASE_DB_PORT", "5432")), dbname="postgres",
                  user=os.environ.get("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
                  password=password, sslmode="require", connect_timeout=20, autocommit=True)
    return PostgresStore(psycopg.connect(**params), open_store)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-db-fingerprint")
    parser.add_argument("--expected-batch-fingerprint")
    args = parser.parse_args(argv)
    verify_revision(args.expected_sha)
    if args.apply and (not check_digest(args.expected_db_fingerprint)
                       or not check_digest(args.expected_batch_fingerprint)):
        raise ProbeError("REVIEWED_DB_AND_BATCH_FINGERPRINTS_REQUIRED")
    token = sync.access_token()
    with open_store() as store:
        report = run_probe(store, token, apply=args.apply,
                           expected_db=args.expected_db_fingerprint,
                           expected_batch=args.expected_batch_fingerprint)
    print(json.dumps({"sha": args.expected_sha, **report}, sort_keys=True))
    return 0 if report["result"] in ("PREVIEW_ONLY", "VERIFIED_BODY_CAPTURE") else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProbeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Database/network/parser errors may contain credentials or source payloads.
        print("FAILED: PROBE_ERROR_DETAILS_WITHHELD_NO_AUTOMATIC_RETRY", file=sys.stderr)
        sys.exit(1)
