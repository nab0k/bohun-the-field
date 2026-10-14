"""Collect I1-I9 into a deterministic, provenance-preserving candidate set."""

import json
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

from . import read_csv, write_csv
from .canonical import candidate_key, canonical_url, hostname, rejected_reason, stable_id

CSV_INPUTS = [
    ("I4", "data/sources/registry.csv"),
    ("I5", "docs/sources/signal-led-source-seeds-20261010.csv"),
    ("I5", "docs/sources/country-source-leads-batch01-20261010.csv"),
    ("I5", "docs/sources/global-source-candidates-20261009.csv"),
    ("I5", "docs/sources/notion-top100-reconciliation-20261009.csv"),
    ("I8", "data/inoreader/source_audit_report.csv"),
]
URL_KEYS = ("feed_url", "xmlurl", "url", "homepage_url", "site_url", "website", "example_source_url", "domain", "hostname", "link")


def _first(row, keys):
    lowered = {str(k).lower(): v for k, v in row.items() if k}
    return next((str(lowered[key]).strip() for key in keys if lowered.get(key)), "")


def _row_candidate(row, provenance):
    feed = _first(row, ("feed_url", "xmlurl"))
    site = _first(row, ("homepage_url", "site_url", "website", "url", "example_source_url", "hostname", "domain", "link"))
    if not site:
        site = feed
    if site and "://" not in site:
        site = "https://" + site
    return {
        "site_url": canonical_url(site), "feed_url": canonical_url(feed),
        "name": _first(row, ("name", "title", "source", "publisher")) or hostname(site),
        "country": _first(row, ("country", "discovery_country", "jurisdiction")),
        "language": _first(row, ("language", "lang")), "provenance": provenance,
        "inoreader_folder": _first(row, ("inoreader_folder", "folder")),
        "tier_hint": _first(row, ("tier_hint", "priority")),
        "signal_hint": _first(row, ("signal_hint", "signal_types", "category", "notion_category")),
        "notes": _first(row, ("notes", "detail", "status", "verification_status")),
        "registry_source_id": _first(row, ("source_id",)),
        "inoreader_stream_id": _first(row, ("inoreader_stream_id",)),
    }


def _json_candidates(path, provenance):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("sources", payload) if isinstance(payload, dict) else payload
    return [_row_candidate(row, provenance) for row in rows if isinstance(row, dict)]


def _opml_candidates(path, provenance):
    root = ET.parse(path).getroot()
    return [_row_candidate({"name": node.get("title") or node.get("text"), "url": node.get("htmlUrl"), "feed_url": node.get("xmlUrl")}, provenance)
            for node in root.iter("outline") if node.get("xmlUrl") or node.get("htmlUrl")]


def _inoreader_candidates(max_calls=20):
    if max_calls < 3:
        raise ValueError("Inoreader collect needs a budget of at least three calls")
    sys.path.insert(0, str(Path("collectors/inoreader").resolve()))
    from sync import access_token, api
    try:
        token = access_token()
        subscriptions = api("subscription/list", token).get("subscriptions", [])
        tags = api("tag/list", token).get("tags", [])
        subscriptions_after = api("subscription/list", token).get("subscriptions", [])
    except RuntimeError as exc:
        if "status=429" in str(exc):
            print("::notice::INOREADER_QUOTA_BLOCKED — continuing without Inoreader")
            return [], {"blocked": True, "calls": 3, "subscriptions": 0, "subscriptions_before": 0, "subscriptions_after": 0, "tags": 0}
        raise
    out = []
    for item in subscriptions:
        feed = item.get("url") or (item.get("id", "")[5:] if item.get("id", "").startswith("feed/") else "")
        folders = [c.get("label", "") for c in item.get("categories", []) if isinstance(c, dict)]
        out.append(_row_candidate({"name": item.get("title"), "url": item.get("htmlUrl"), "feed_url": feed,
                                   "inoreader_folder": "|".join(filter(None, folders)),
                                   "inoreader_stream_id": item.get("id", ""),
                                   "notes": f"firstitemmsec={item.get('firstitemmsec', '')}"}, "I1|I2"))
    return out, {"blocked": False, "calls": 3, "subscriptions": len(subscriptions), "subscriptions_before": len(subscriptions), "subscriptions_after": len(subscriptions_after), "subscription_delta": len(subscriptions_after) - len(subscriptions), "tags": len(tags)}


def collect(root=".", *, use_inoreader=False, quota_confirmed=False, max_candidates=2000, inoreader_call_budget=20):
    root = Path(root)
    raw = []
    for provenance, relative in CSV_INPUTS:
        raw.extend(_row_candidate(row, provenance) for row in read_csv(root / relative))
    for relative in ("collectors/inoreader/top100_sources.json", "collectors/web_monitor/sources.json"):
        if (root / relative).exists():
            raw.extend(_json_candidates(root / relative, "I6" if "top100" in relative else "I8"))
    for relative in ("data/inoreader/bohun-feeds.opml",):
        if (root / relative).exists():
            raw.extend(_opml_candidates(root / relative, "I8"))
    hunter = root / "output/source-hunter/validated-feeds.csv"
    if hunter.exists():
        raw.extend(_row_candidate(row, "I3") for row in read_csv(hunter))
    inbox = root / "data/sources/inbox"
    if inbox.exists():
        for path in sorted(inbox.glob("*.csv")):
            raw.extend(_row_candidate(row, "I9:" + path.name) for row in read_csv(path))
    inoreader_meta = {"blocked": False, "calls": 0, "subscriptions": 0, "subscriptions_before": 0, "subscriptions_after": 0, "subscription_delta": 0, "tags": 0}
    if use_inoreader:
        if not quota_confirmed:
            print("::notice::INOREADER_SKIPPED — quota_confirmed=false")
            inoreader_meta["blocked"] = True
        else:
            rows, inoreader_meta = _inoreader_candidates(inoreader_call_budget)
            raw.extend(rows)

    registry = {}
    for row in read_csv(root / "data/sources/registry.csv"):
        registry.setdefault(hostname(row.get("homepage_url") or row.get("feed_url")), row.get("source_id", ""))
    merged, rejected = OrderedDict(), []
    for row in raw:
        target = row["site_url"] or row["feed_url"]
        reason = rejected_reason(target)
        if reason:
            rejected.append({**row, "reason": reason})
            continue
        key = candidate_key(row["site_url"], row["feed_url"])
        if not key[0]:
            rejected.append({**row, "reason": "invalid_url"})
            continue
        if key not in merged:
            row["site_id"] = stable_id("site_", key[0])
            row["candidate_id"] = stable_id("cand_", *key)
            row["already_in_registry"] = "true" if key[0] in registry else "false"
            row["registry_source_id"] = registry.get(key[0], row.get("registry_source_id", ""))
            merged[key] = row
        else:
            current = merged[key]
            current["provenance"] = "|".join(sorted(set(current["provenance"].split("|") + row["provenance"].split("|"))))
            for field in ("name", "country", "language", "inoreader_folder", "inoreader_stream_id", "tier_hint", "signal_hint", "notes"):
                if not current.get(field) and row.get(field):
                    current[field] = row[field]
    candidates = list(merged.values())[:max_candidates]
    return candidates, rejected, inoreader_meta


def run(output_dir, **kwargs):
    candidates, rejected, meta = collect(**kwargs)
    fields = ["candidate_id", "site_id", "site_url", "feed_url", "name", "country", "language", "provenance", "inoreader_folder", "already_in_registry", "registry_source_id", "inoreader_stream_id", "tier_hint", "signal_hint", "notes"]
    output = Path(output_dir)
    write_csv(output / "candidates.csv", candidates, fields)
    write_csv(output / "rejected.csv", rejected, fields + ["reason"])
    (output / "collect-meta.json").write_text(json.dumps({"candidates": len(candidates), "rejected": len(rejected), "inoreader": meta}, indent=2), encoding="utf-8")
    return candidates
