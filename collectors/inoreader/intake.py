"""Manual private intake CLI. No schedule, models, Notion or publication."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

from .intake_contract import (IntakeError, VERSION, MAX_ITEMS, prepare_batch, require,
                              uuid_string, validate_batch)
from .intake_store import PostgresIntakeStore


def summary(batch, *, existing=False):
    manifest = validate_batch(batch)
    bodies = sum(json.loads(p)["item"]["raw_data"]["content_capture"]["status"] ==
                 "BODY_PRESENT_COMPLETENESS_UNVERIFIED" for p in batch.payloads)
    return {"batch_id": batch.batch_id, "manifest_sha256": batch.manifest_sha256,
            "items": manifest["item_count"], "body_items": bodies,
            "payload_bytes": manifest["payload_bytes"], "contract_version": VERSION,
            "existing_batch": existing,
            "result": "BODY_SNAPSHOT_READBACK" if bodies else "INCONCLUSIVE_NO_BODY"}


def capture_new(store, batch_id, producer_sha, pipeline_version):
    # Validate invocation before even a DB SELECT or an OAuth request.
    prepare_batch([], batch_id=batch_id, producer_sha=producer_sha,
                  pipeline_version=pipeline_version, collected_at=datetime.now(timezone.utc))
    # An existing immutable batch is read without OAuth or a provider call.
    existing = store.read(batch_id)
    if existing is not None:
        return summary(existing[0], existing=True)
    from . import probe, sync
    from .content_contract import CaptureBudget
    token = sync.access_token()
    unique, _ = probe.collect(token)  # Same bounded 3 folders/one page/20.
    def has_body(pair):
        body = pair[0].get("summary")
        return isinstance(body, dict) and isinstance(body.get("content"), str) and bool(body["content"].strip())
    pairs = sorted(unique.values(), key=lambda pair: not has_body(pair))[:MAX_ITEMS]
    now = datetime.now(timezone.utc)
    budget = CaptureBudget()
    items = [sync.normalize(raw, folder, captured_at=now, content_budget=budget) for raw, folder in pairs]
    batch = prepare_batch(items, batch_id=batch_id, producer_sha=producer_sha,
                          pipeline_version=pipeline_version, collected_at=now)
    store.persist(batch)
    return summary(batch)


def open_store():
    # Deliberately never fall back to the old full-access SUPABASE_DB_PASSWORD.
    dsn = os.environ.get("BOHUN_INTAKE_DSN")
    require(bool(dsn), "INTAKE_LIMITED_DSN_REQUIRED")
    try:
        import psycopg
    except ImportError:
        raise IntakeError("INTAKE_DRIVER_UNAVAILABLE") from None
    return PostgresIntakeStore(lambda: psycopg.connect(dsn, connect_timeout=10, autocommit=False))


def verify_revision(expected_sha):
    from . import probe
    probe.verify_revision(expected_sha)
    paths = ["collectors/inoreader/" + name for name in
             ("intake.py", "intake_contract.py", "intake_store.py")]
    try:
        subprocess.check_output(["git", "ls-files", "--error-unmatch", *paths],
                                cwd=Path(__file__).resolve().parents[2], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        raise IntakeError("INTAKE_COMMITTED_ENTRYPOINT_REQUIRED") from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "read"))
    parser.add_argument("--batch-id", required=True, help="Preallocated UUID; recovery uses read with this ID")
    parser.add_argument("--expected-sha", required=True, help="Separately reviewed full feature commit SHA")
    parser.add_argument("--pipeline-version", default="intake-v1")
    parser.add_argument("--allow-live", action="store_true", help="Explicitly enable separately authorized DB/API access")
    args = parser.parse_args(argv)
    uuid_string(args.batch_id)
    require(args.allow_live, "INTAKE_LIVE_ACCESS_NOT_ENABLED")
    verify_revision(args.expected_sha)
    store = open_store()
    if args.mode == "capture":
        report = capture_new(store, args.batch_id, args.expected_sha, args.pipeline_version)
    else:
        stored = store.read(args.batch_id)
        require(stored is not None, "INTAKE_BATCH_NOT_FOUND_DO_NOT_REFETCH")
        report = summary(stored[0], existing=True)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["body_items"] else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except IntakeError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        print("FAILED: INTAKE_ERROR_DETAILS_WITHHELD_NO_AUTOMATIC_RETRY", file=sys.stderr)
        sys.exit(1)
