"""Explainable title clustering without external models."""

import datetime as dt
import re
import unicodedata
from collections import defaultdict

from .canonical import stable_id

SUFFIX = re.compile(r"\s+(?:-|\||—)\s+[\w .&'-]{2,60}$", re.UNICODE)


def normalize_title(value):
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = SUFFIX.sub("", value)
    return " ".join(re.findall(r"\w+", value, re.UNICODE))


def shingles(value, size=3):
    words = normalize_title(value).split()
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[index:index + size]) for index in range(len(words) - size + 1)}


def similarity(left, right):
    a, b = shingles(left), shingles(right)
    return len(a & b) / len(a | b) if a and b else 0.0


def _date(value):
    try:
        return dt.datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def cluster_items(items, threshold=0.6, window_hours=72):
    ordered = sorted(items, key=lambda row: (row.get("published_at") or row.get("fetched_at") or "", row.get("item_id", "")))
    groups = []
    for item in ordered:
        title_norm = normalize_title(item.get("title"))
        match = None
        relation = "independent"
        for group in reversed(groups):
            representative = group["items"][0]
            left_date, right_date = _date(item.get("published_at")), _date(representative.get("published_at"))
            if left_date and right_date and abs((left_date - right_date).total_seconds()) > window_hours * 3600:
                continue
            if item.get("canonical_url") and item.get("canonical_url") == representative.get("canonical_url"):
                match, relation = group, "copy"; break
            if title_norm and title_norm == normalize_title(representative.get("title")):
                match, relation = group, "copy"; break
            if similarity(item.get("title"), representative.get("title")) >= threshold:
                match, relation = group, "reprint"; break
        if match is None:
            groups.append({"items": [item], "relations": ["independent"]})
        else:
            match["items"].append(item); match["relations"].append(relation)
    clusters, membership = [], []
    for group in groups:
        group_items = group["items"]
        first = min(group_items, key=lambda row: (row.get("published_at") or row.get("fetched_at") or "", row.get("feed_id", "")))
        cluster_id = stable_id("cluster_", normalize_title(first.get("title")), first.get("published_at", "")[:10])
        publishers = {row.get("feed_id") for row in group_items if row.get("feed_id")}
        clusters.append({
            "cluster_id": cluster_id, "title": first.get("title", ""), "first_seen_feed": first.get("feed_id", ""),
            "first_seen_at": first.get("published_at") or first.get("fetched_at", ""), "size": len(group_items),
            "publishers_count": len(publishers),
        })
        for row, relation in zip(group_items, group["relations"]):
            membership.append({"cluster_id": cluster_id, "item_id": row.get("item_id", ""), "feed_id": row.get("feed_id", ""), "relation": relation})
    return clusters, membership

