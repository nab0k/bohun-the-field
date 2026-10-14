#!/usr/bin/env python3
"""BOHUN Source Harvest v1 command line."""

import argparse
import csv
import gzip
import json
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors.source_harvest import read_csv, write_csv
from collectors.source_harvest import backlog, citations, cluster, discover, inputs, relevance, report, yield_metrics
from collectors.source_harvest.relevance_llm import adjudicate


def read_items(path):
    with gzip.open(path, "rt", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def analyze(work, days_back, *, use_gemini=False, gemini_cap_usd=0):
    items = read_items(work / "items.csv.gz")
    clusters, membership = cluster.cluster_items(items)
    clusters = relevance.classify_clusters(clusters)
    gemini_spend = 0
    if use_gemini:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required when use_gemini=true")
        unsure = [row for row in clusters if row["relevant"] == "unsure"]
        decisions, ledger = adjudicate(unsure, api_key=api_key, cap_usd=gemini_cap_usd)
        by_id = {row["cluster_id"]: row for row in decisions}
        for row in clusters:
            if row["cluster_id"] in by_id:
                decision = by_id[row["cluster_id"]]
                row.update({key: decision.get(key, row.get(key, "")) for key in ("relevant", "signal", "region")})
                row["rule_ids"] = (row.get("rule_ids", "") + "|gemini:unsure-adjudication").strip("|")
        gemini_spend = ledger.spent_usd
        (work / "gemini-budget.json").write_text(json.dumps({"cap_usd": gemini_cap_usd, "reserved_usd": gemini_spend, "entries": ledger.entries}, indent=2), encoding="utf-8")
    coverage = read_csv(work / "backlog-coverage.csv")
    metrics = yield_metrics.calculate(items, clusters, membership, coverage, days_back)
    feeds = read_csv(work / "validated-feeds.csv")
    site_metrics = yield_metrics.aggregate_sites(items, clusters, membership, coverage, feeds, days_back)
    matrix = yield_metrics.coverage_matrix(clusters, metrics)
    candidates = read_csv(work / "candidates.csv")
    citation_rows = citations.mine(items, metrics, candidates)
    write_csv(work / "clusters.csv", clusters)
    write_csv(work / "cluster-membership.csv", membership)
    write_csv(work / "channel-yield.csv", metrics)
    write_csv(work / "site-yield.csv", site_metrics)
    write_csv(work / "coverage-matrix.csv", matrix)
    signals = sorted(relevance.load_rules()["signals"]); regions = sorted(relevance.load_rules()["regions"])
    present = {(row["signal"], row["region"]) for row in matrix}
    write_csv(work / "empty-cells.csv", ({"signal": signal, "region": region} for signal in signals for region in regions if (signal, region) not in present), ["signal", "region"])
    write_csv(work / "citation-domains.csv", citation_rows, ["domain", "cited_by_channels", "citations", "example_url"])
    site_status = {row["site_id"]: row["status"] for row in site_metrics}
    pending_sites = {row["site_id"] for row in read_csv(work / "pending-no-rss.csv")}
    candidate_status = [{"candidate_id": row["candidate_id"], "site_id": row["site_id"],
                         "status": site_status.get(row["site_id"], "no_rss" if row["site_id"] in pending_sites else "not_analyzed"),
                         "reason": "yield_metrics" if row["site_id"] in site_status else "feed_not_found" if row["site_id"] in pending_sites else "missing_analysis"}
                        for row in candidates]
    write_csv(work / "candidate-status.csv", candidate_status, ["candidate_id", "site_id", "status", "reason"])
    by_cluster = {}
    for row in membership:
        by_cluster.setdefault(row["cluster_id"], []).append(row)
    pairs = []
    for cluster_id, rows in by_cluster.items():
        for index in range(len(rows) - 1):
            pairs.append({"cluster_id_left": cluster_id, "cluster_id_right": cluster_id, "item_id_left": rows[index]["item_id"], "item_id_right": rows[index + 1]["item_id"], "relation": rows[index + 1]["relation"], "owner_label": ""})
    first_rows = [rows[0] for rows in by_cluster.values() if rows]
    for index in range(len(first_rows) - 1):
        pairs.append({"cluster_id_left": first_rows[index]["cluster_id"], "cluster_id_right": first_rows[index + 1]["cluster_id"], "item_id_left": first_rows[index]["item_id"], "item_id_right": first_rows[index + 1]["item_id"], "relation": "independent", "owner_label": ""})
    random.Random(20261014).shuffle(pairs)
    write_csv(work / "cluster-sample.csv", pairs[:50], ["cluster_id_left", "cluster_id_right", "item_id_left", "item_id_right", "relation", "owner_label"])
    return gemini_spend


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("collect", "discover", "backlog", "analyze", "report", "all"))
    parser.add_argument("--work-dir", default="output/source-harvest")
    parser.add_argument("--days-back", type=int, choices=(30, 45, 60), default=45)
    parser.add_argument("--max-candidates", type=int, default=2000)
    parser.add_argument("--use-inoreader", action="store_true")
    parser.add_argument("--quota-confirmed", action="store_true")
    parser.add_argument("--inoreader-call-budget", type=int, default=20)
    parser.add_argument("--use-inoreader-history", action="store_true")
    parser.add_argument("--use-gemini", action="store_true")
    parser.add_argument("--gemini-cap-usd", type=float, default=0)
    args = parser.parse_args()
    work = Path(args.work_dir); work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic(); requests = 0; gemini_spend = 0
    runtime_path = work / "runtime-meta.json"
    runtime = json.loads(runtime_path.read_text(encoding="utf-8")) if runtime_path.exists() else {"duration_seconds": 0, "http_requests": 0}
    commands = ("collect", "discover", "backlog", "analyze", "report") if args.command == "all" else (args.command,)
    for command in commands:
        if command == "collect":
            inputs.run(work, root=ROOT, use_inoreader=args.use_inoreader, quota_confirmed=args.quota_confirmed, max_candidates=args.max_candidates, inoreader_call_budget=args.inoreader_call_budget)
        elif command == "discover":
            _, _, count = discover.run(work / "candidates.csv", work); requests += count
        elif command == "backlog":
            items, coverage_rows, count = backlog.run(work / "validated-feeds.csv", work, days_back=args.days_back); requests += count
            if args.use_inoreader_history:
                if not args.quota_confirmed:
                    print("::notice::INOREADER_HISTORY_SKIPPED — quota_confirmed=false")
                    continue
                meta = json.loads((work / "collect-meta.json").read_text(encoding="utf-8"))
                feeds = read_csv(work / "validated-feeds.csv"); candidates = read_csv(work / "candidates.csv")
                extra, calls, blocked = backlog.inoreader_history(feeds, coverage_rows, candidates, items, call_budget=args.inoreader_call_budget, calls_used=meta.get("inoreader", {}).get("calls", 0))
                if extra:
                    items.extend(extra)
                    with gzip.open(work / "items.csv.gz", "wt", newline="", encoding="utf-8") as handle:
                        writer = csv.DictWriter(handle, fieldnames=backlog.ITEM_FIELDS); writer.writeheader(); writer.writerows(items)
                meta["inoreader"]["calls"] = meta["inoreader"].get("calls", 0) + calls
                meta["inoreader"]["blocked"] = meta["inoreader"].get("blocked", False) or blocked
                (work / "collect-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        elif command == "analyze":
            gemini_spend = analyze(work, args.days_back, use_gemini=args.use_gemini, gemini_cap_usd=args.gemini_cap_usd)
        elif command == "report":
            if (work / "gemini-budget.json").exists():
                gemini_spend = json.loads((work / "gemini-budget.json").read_text()).get("reserved_usd", 0)
            report.build(work, duration_seconds=round(runtime["duration_seconds"] + time.monotonic() - started, 2), request_count=runtime["http_requests"] + requests, gemini_spend=gemini_spend)
    if args.command != "report":
        runtime["duration_seconds"] = round(runtime["duration_seconds"] + time.monotonic() - started, 2)
        runtime["http_requests"] += requests
        runtime_path.write_text(json.dumps(runtime, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
