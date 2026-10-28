#!/usr/bin/env python3
"""Offline replay by default; explicit runtime subcommand requires a reviewed live plan."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors.intelligence_pipeline.contracts import Document, Usage, exact_fields, json_text
from collectors.intelligence_pipeline.quota import Limits, MemoryQuota
from collectors.intelligence_pipeline.testing import MemoryStore, ReplayModel
from collectors.intelligence_pipeline.worker import Worker


def replay(data):
    exact_fields(data, {"mode", "documents", "responses", "limits"})
    if data["mode"] != "synthetic-replay":
        raise ValueError("Only explicitly synthetic offline replay is available")
    if not isinstance(data["documents"], list) or not 1 <= len(data["documents"]) <= 100:
        raise ValueError("Provide 1..100 synthetic documents")
    if not isinstance(data["responses"], dict):
        raise ValueError("Supplied replay responses required")
    exact_fields(data["limits"], {"requests", "usage"})
    quota = MemoryQuota("synthetic-account", Limits(data["limits"]["requests"], Usage(**data["limits"]["usage"])))
    store, model = MemoryStore(), ReplayModel(data["responses"])
    now = datetime.now(timezone.utc)
    worker = Worker(store, model, quota, clock=lambda: now, worker_id="offline-replay")
    for raw in data["documents"]:
        doc = {**raw, "verified_entity_ids": tuple(raw.get("verified_entity_ids", []))}
        worker.submit(Document(**doc))
    # A bounded replay; deferrals remain visible, no sleeping or retry loop.
    steps = []
    for _ in range(3 * len(data["documents"])):
        status = worker.tick()
        if status == "IDLE":
            break
        steps.append(status)
    return {"mode": "synthetic-replay", "runtime": "in-memory-reference-only",
            "live_model_run": "NOT_RUN", "notion_write": "NOT_RUN", "public_release": False,
            "steps": steps, "model_calls": len(model.calls), "quota": quota.snapshot(now),
            "jobs": store.snapshot()}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "runtime":
        return runtime_main(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="Explicit synthetic replay JSON, max 5 MiB")
    parser.add_argument("--report", type=Path, required=True, help="New PRIVATE report path outside the Git checkout")
    args = parser.parse_args(argv)
    try:
        report_path = args.report.resolve()
        if report_path.is_relative_to(ROOT):
            raise ValueError("Report must be outside the Git checkout/public/dist")
        with args.input.open("rb") as handle:
            raw = handle.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise ValueError("Replay input exceeds 5 MiB")
        report = replay(json.loads(raw))
        content = json_text(report) + "\n"
        report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Exclusive creation prevents accidental overwrite; never dump source text to stdout.
        fd = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        print(f"SYNTHETIC REPLAY: calls={report['model_calls']}, public_release=false, report={report_path}")
        return 0
    except (OSError, ValueError, TypeError, KeyError):
        print("Replay refused/failed: check JSON contracts and use a new private report path. No network called.", file=sys.stderr)
        return 2


def runtime_main(argv):
    parser = argparse.ArgumentParser(description="INPUT29: one reviewed runtime with explicit FREE_ONLY or CAPPED_PAID access; no fallback")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--allow-live", action="store_true")
    args = parser.parse_args(argv)
    try:
        if not args.allow_live:
            raise ValueError("LIVE_FLAG_REQUIRED")
        with args.config.open("rb") as handle:
            data = handle.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            raise ValueError("CONFIG_TOO_LARGE")
        from collectors.intelligence_pipeline.runtime import live_run
        result = live_run(json.loads(data), allow_live=True, expected_sha=args.expected_sha)
        print(json_text(result))  # Only IDs/counts/reason codes, no packets or secrets.
        return 0
    except Exception:
        print("RUNTIME_STOP_DETAILS_WITHHELD_NO_AUTOMATIC_RETRY", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
