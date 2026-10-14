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
    names = {}
    for row in candidates:
        name = row.get("name") or row.get("site_url", "")
        if row.get("site_id") not in names or names[row.get("site_id")].lower().startswith("comments"):
            names[row.get("site_id")] = name
    for row in metrics:
        row["name"] = names.get(row.get("site_id"), "")
    top = sorted(metrics, key=lambda row: -float(row.get("unique_relevant_per_week") or 0))[:30]
    order = ["none", "<7d", "7-13d", "14-29d", ">=30d"]
    history = Counter(row.get("history_bucket") or "none" for row in metrics)
    history_dist = {key: history.get(key, 0) for key in order}
    history_sources = Counter(row.get("history_source") or "feed" for row in coverage)
    report = {
        "funnel": {"candidates": len(candidates), "rejected": len(rejected), "feeds_valid": len(feeds), "no_rss": sum(row.get("method_tried") != "deadline_not_attempted" for row in pending),
                   "not_attempted_deadline": sum(row.get("method_tried") == "deadline_not_attempted" for row in pending),
                   "coverage_14d": sum(float(row.get("backlog_coverage_days") or 0) >= 14 for row in coverage)},
        "history_depth": history_dist, "history_sources": dict(history_sources),
        "statuses": dict(statuses), "discovery_methods": dict(methods), "input_provenance": dict(provenance),
        "top_channels": top, "coverage_cells": len(matrix), "inoreader": meta.get("inoreader", {}),
        "runtime": {"duration_seconds": duration_seconds, "http_requests": request_count, "gemini_spend_usd": gemini_spend},
        "limitations": ["Feed history can be shorter than the requested window.", "robots.txt, 403 and 429 responses reduce coverage.", "This is a candidate assessment, not an approved registry."],
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# BOHUN Source Harvest v1", "", "This output ranks candidates; it is not an approved source registry.", "",
             "## Funnel", "", f"Candidates {len(candidates)} → valid feeds {len(feeds)} → ≥14 day history {report['funnel']['coverage_14d']}.", "",
             "## Statuses", "", ", ".join(f"{key}: {value}" for key, value in sorted(statuses.items())) or "No analyzed channels.", "",
             "## History depth per channel", "", " · ".join(f"{key}: {value}" for key, value in history_dist.items()), "",
             "## Top channels", "", "| name | feed | unique relevant/week | history | confidence | status |", "|---|---|---:|---|---|---|"]
    lines.extend(f"| {row.get('name', '')} | {row.get('feed_url', '')} | {row['unique_relevant_per_week']} | {row.get('history_bucket', '')} | {row['confidence']} | {row['status']} |" for row in top)
    lines += ["", "## Limitations", ""] + [f"- {value}" for value in report["limitations"]]
    (output / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"::notice::HARVEST_FUNNEL candidates={len(candidates)} valid_feeds={len(feeds)} coverage_14d={report['funnel']['coverage_14d']} no_rss={report['funnel']['no_rss']} not_attempted={report['funnel']['not_attempted_deadline']}")
    print("::notice::HARVEST_STATUS " + " ".join(f"{key}={value}" for key, value in sorted(statuses.items())))
    print("::notice::HARVEST_HISTORY " + " ".join(f"{key}={value}" for key, value in history_dist.items()) + " sources=" + ",".join(f"{k}:{v}" for k, v in sorted(history_sources.items())))
    # Full channel table as compact notices (artifacts are not always downloadable): name|bucket|unique/wk|items|status
    rows = sorted(metrics, key=lambda row: (-float(row.get("unique_relevant_per_week") or 0), row.get("name", "")))
    lines_ = [f"{(row.get('name') or row.get('feed_url', ''))[:40]}|{row.get('history_bucket', '')}|{row.get('unique_relevant_per_week')}|{row.get('items')}|{row.get('status')}" for row in rows]
    chunk, chunks = [], []
    for line in lines_:
        if sum(len(x) + 3 for x in chunk) + len(line) > 3500:
            chunks.append(chunk); chunk = []
        chunk.append(line)
    if chunk:
        chunks.append(chunk)
    for index, part in enumerate(chunks[:9], 1):
        print(f"::notice title=HARVEST_CHANNELS_{index}::" + " ;; ".join(part))
    prov_feeds = Counter()
    site_prov = {row.get("site_id"): row.get("provenance", "") for row in candidates}
    for row in feeds:
        for part in site_prov.get(row.get("site_id"), "").split("|"):
            if part:
                prov_feeds[part.split(":")[0] if not part.startswith("I9") else part] += 1
    print("::notice::HARVEST_FEEDS_BY_INPUT " + " ".join(f"{k}={v}" for k, v in sorted(prov_feeds.items())))
    keep_by = Counter()
    for row in metrics:
        if row.get("status") == "keep_candidate":
            for part in site_prov.get(row.get("site_id"), "").split("|"):
                if part:
                    keep_by[part.split(":")[0] if not part.startswith("I9") else part] += 1
    print("::notice::HARVEST_KEEP_BY_INPUT " + " ".join(f"{k}={v}" for k, v in sorted(keep_by.items())))
    top_notice = ", ".join(f"{row.get('name') or row['feed_id']}={row['unique_relevant_per_week']}/wk" for row in top[:15])
    print("::notice::HARVEST_TOP10 " + (top_notice or "none"))
    print(f"::notice::HARVEST_EMPTY_CELLS count={len(empty)} examples=" + ", ".join(f"{row['signal']}×{row['region']}" for row in empty[:12]))
    print("::notice::INOREADER subscriptions_before={subscriptions_before} subscriptions_after={subscriptions_after} delta={subscription_delta} calls={calls} blocked={blocked}".format(**({"subscriptions_before": 0, "subscriptions_after": 0, "subscription_delta": 0, "calls": 0, "blocked": False} | meta.get("inoreader", {}))))
    return report
