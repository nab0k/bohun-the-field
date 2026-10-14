"""Owner-facing report and compact GitHub Actions notices."""

import json
from collections import Counter
from pathlib import Path

from . import read_csv


def build(output_dir, *, duration_seconds=0, request_count=0, gemini_spend=0):
    output = Path(output_dir)
    candidates = read_csv(output / "candidates.csv")
    rejected = read_csv(output / "rejected.csv")
    feeds = read_csv(output / "validated-feeds.csv")
    pending = read_csv(output / "pending-no-rss.csv")
    coverage = read_csv(output / "backlog-coverage.csv")
    metrics = read_csv(output / "channel-yield.csv")
    matrix = read_csv(output / "coverage-matrix.csv")
    empty = read_csv(output / "empty-cells.csv")
    meta = json.loads((output / "collect-meta.json").read_text()) if (output / "collect-meta.json").exists() else {"inoreader": {}}
    statuses = Counter(row.get("status") for row in metrics)
    methods = Counter(row.get("discovery_method") for row in feeds)
    provenance = Counter(part for row in candidates for part in row.get("provenance", "").split("|") if part)
    top = sorted(metrics, key=lambda row: -float(row.get("unique_relevant_per_week") or 0))[:30]
    report = {
        "funnel": {"candidates": len(candidates), "rejected": len(rejected), "feeds_valid": len(feeds), "no_rss": len(pending),
                   "coverage_14d": sum(float(row.get("backlog_coverage_days") or 0) >= 14 for row in coverage)},
        "statuses": dict(statuses), "discovery_methods": dict(methods), "input_provenance": dict(provenance),
        "top_channels": top, "coverage_cells": len(matrix), "inoreader": meta.get("inoreader", {}),
        "runtime": {"duration_seconds": duration_seconds, "http_requests": request_count, "gemini_spend_usd": gemini_spend},
        "limitations": ["Feed history can be shorter than the requested window.", "robots.txt, 403 and 429 responses reduce coverage.", "This is a candidate assessment, not an approved registry."],
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# BOHUN Source Harvest v1", "", "This output ranks candidates; it is not an approved source registry.", "",
             "## Funnel", "", f"Candidates {len(candidates)} → valid feeds {len(feeds)} → ≥14 day history {report['funnel']['coverage_14d']}.", "",
             "## Statuses", "", ", ".join(f"{key}: {value}" for key, value in sorted(statuses.items())) or "No analyzed channels.", "",
             "## Top channels", "", "| feed_id | unique relevant/week | confidence | status |", "|---|---:|---|---|"]
    lines.extend(f"| {row['feed_id']} | {row['unique_relevant_per_week']} | {row['confidence']} | {row['status']} |" for row in top)
    lines += ["", "## Limitations", ""] + [f"- {value}" for value in report["limitations"]]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"::notice::HARVEST_FUNNEL candidates={len(candidates)} valid_feeds={len(feeds)} coverage_14d={report['funnel']['coverage_14d']} no_rss={len(pending)}")
    print("::notice::HARVEST_STATUS " + " ".join(f"{key}={value}" for key, value in sorted(statuses.items())))
    top_notice = ", ".join(f"{row['feed_id']}={row['unique_relevant_per_week']}/wk" for row in top[:10])
    print("::notice::HARVEST_TOP10 " + (top_notice or "none"))
    print(f"::notice::HARVEST_EMPTY_CELLS count={len(empty)} examples=" + ", ".join(f"{row['signal']}×{row['region']}" for row in empty[:12]))
    print("::notice::INOREADER subscriptions_before={subscriptions_before} subscriptions_after={subscriptions_after} delta={subscription_delta} calls={calls} blocked={blocked}".format(**({"subscriptions_before": 0, "subscriptions_after": 0, "subscription_delta": 0, "calls": 0, "blocked": False} | meta.get("inoreader", {}))))
    return report
