"""Retrospective per-feed yield metrics."""

import datetime as dt
from collections import defaultdict


HISTORY_BUCKETS = ((7, "<7d"), (14, "7-13d"), (30, "14-29d"))


def history_bucket(days):
    days = float(days or 0)
    if days <= 0:
        return "none"
    return next((label for limit, label in HISTORY_BUCKETS if days < limit), ">=30d")


def calculate(items, clusters, membership, coverage_rows, days_back=45, feeds=None):
    """Per-feed metrics. Every validated feed gets a row, including feeds with zero items."""
    feed_meta = {row["feed_id"]: row for row in (feeds or [])}
    cluster_by_id = {row["cluster_id"]: row for row in clusters}
    members_by_feed = defaultdict(list)
    for row in membership:
        members_by_feed[row["feed_id"]].append(row)
    item_count = defaultdict(int)
    for row in items:
        item_count[row["feed_id"]] += 1
    coverage = {row["feed_id"]: float(row.get("backlog_coverage_days") or 0) for row in coverage_rows}
    weeks = max(days_back / 7, 1 / 7)
    results = []
    for feed_id in sorted(set(item_count) | set(members_by_feed) | set(coverage) | set(feed_meta)):
        memberships = members_by_feed[feed_id]
        relevant = {row["cluster_id"] for row in memberships if cluster_by_id.get(row["cluster_id"], {}).get("relevant") == "yes"}
        first = {cluster_id for cluster_id in relevant if cluster_by_id[cluster_id].get("first_seen_feed") == feed_id}
        noise = sum(cluster_by_id.get(row["cluster_id"], {}).get("relevant") == "no" for row in memberships)
        dup = sum(row.get("relation") in ("copy", "reprint") for row in memberships)
        cells = sorted({f"{cluster_by_id[c].get('signal')}×{cluster_by_id[c].get('region')}" for c in relevant})
        count = item_count[feed_id]
        unique_per_week = len(first) / weeks
        confidence = "low" if coverage.get(feed_id, 0) < 14 or count < 20 else "medium"
        if unique_per_week >= 1 and (noise / max(len(memberships), 1)) <= .7:
            status = "keep_candidate"
        elif first or confidence == "low":
            status = "probation"
        else:
            status = "archive_candidate"
        if count == 0:
            status = "no_items"
        meta = feed_meta.get(feed_id, {})
        results.append({
            "feed_id": feed_id, "site_id": meta.get("site_id", ""), "feed_url": meta.get("feed_url", ""),
            "history_bucket": history_bucket(coverage.get(feed_id, 0)),
            "items_per_week": round(count / weeks, 3),
            "relevant_clusters_per_week": round(len(relevant) / weeks, 3),
            "unique_relevant_per_week": round(unique_per_week, 3),
            "first_seen_share": round(len(first) / max(len(relevant), 1), 3),
            "noise_share": round(noise / max(len(memberships), 1), 3),
            "dup_share": round(dup / max(len(memberships), 1), 3),
            "matrix_cells": "|".join(cells), "confidence": confidence, "status": status,
            "backlog_coverage_days": coverage.get(feed_id, 0), "items": count,
        })
    return results


def coverage_matrix(clusters, metrics):
    channels = defaultdict(set); counts = defaultdict(float)
    for row in metrics:
        for cell in filter(None, row.get("matrix_cells", "").split("|")):
            channels[cell].add(row["feed_id"])
            counts[cell] += row["unique_relevant_per_week"]
    rows = []
    for cell in sorted(channels):
        signal, region = cell.split("×", 1)
        rows.append({"signal": signal, "region": region, "unique_events_per_week": round(counts[cell], 3), "channels": len(channels[cell])})
    return rows


def aggregate_sites(items, clusters, membership, coverage_rows, feeds, days_back=45):
    site_by_feed = {row["feed_id"]: row["site_id"] for row in feeds}
    feeds_by_site = defaultdict(set)
    for feed_id, site_id in site_by_feed.items():
        feeds_by_site[site_id].add(feed_id)
    cluster_by_id = {row["cluster_id"]: row for row in clusters}
    members_by_site = defaultdict(list)
    for row in membership:
        members_by_site[site_by_feed.get(row["feed_id"], "")].append(row)
    item_count = defaultdict(int)
    for row in items:
        item_count[site_by_feed.get(row["feed_id"], "")] += 1
    coverage = {row["feed_id"]: float(row.get("backlog_coverage_days") or 0) for row in coverage_rows}
    weeks = max(days_back / 7, 1 / 7)
    out = []
    for site_id, site_feeds in sorted(feeds_by_site.items()):
        if not site_id:
            continue
        members = members_by_site[site_id]
        relevant = {row["cluster_id"] for row in members if cluster_by_id.get(row["cluster_id"], {}).get("relevant") == "yes"}
        first = {cluster_id for cluster_id in relevant if cluster_by_id[cluster_id].get("first_seen_feed") in site_feeds}
        noise = sum(cluster_by_id.get(row["cluster_id"], {}).get("relevant") == "no" for row in members)
        dup = sum(row.get("relation") in ("copy", "reprint") for row in members)
        cells = sorted({f"{cluster_by_id[value].get('signal')}×{cluster_by_id[value].get('region')}" for value in relevant})
        count = item_count[site_id]
        confidence = "low" if max((coverage.get(feed_id, 0) for feed_id in site_feeds), default=0) < 14 or count < 20 else "medium"
        unique_per_week = len(first) / weeks
        if unique_per_week >= 1 and noise / max(len(members), 1) <= .7:
            status = "keep_candidate"
        elif first or confidence == "low":
            status = "probation"
        else:
            status = "archive_candidate"
        out.append({
            "site_id": site_id, "feeds": len(site_feeds),
            "items_per_week": round(count / weeks, 3),
            "relevant_clusters_per_week": round(len(relevant) / weeks, 3),
            "unique_relevant_per_week": round(unique_per_week, 3),
            "first_seen_share": round(len(first) / max(len(relevant), 1), 3),
            "noise_share": round(noise / max(len(members), 1), 3),
            "dup_share": round(dup / max(len(members), 1), 3),
            "matrix_cells": "|".join(cells),
            "confidence": confidence,
            "status": status,
        })
    return out
