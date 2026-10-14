"""Mine cited domains from feed metadata only; article pages are never fetched."""

import json
from collections import defaultdict
from urllib.parse import urlsplit

from .canonical import REJECT_HOSTS, hostname


def mine(items, metrics, candidates, limit=200):
    keep = {row["feed_id"] for row in metrics if row.get("status") == "keep_candidate"}
    known = {hostname(row.get("site_url") or row.get("feed_url")) for row in candidates}
    found = defaultdict(lambda: {"channels": set(), "citations": 0, "example_url": ""})
    for item in items:
        if item.get("feed_id") not in keep:
            continue
        try:
            links = json.loads(item.get("summary_links") or "[]")
        except json.JSONDecodeError:
            links = []
        for url in links:
            domain = (urlsplit(url).hostname or "").lower().removeprefix("www.")
            if not domain or domain in known or domain in REJECT_HOSTS or any(domain.endswith("." + value) for value in REJECT_HOSTS):
                continue
            if any(token in domain for token in ("cloudfront", "akamai", "cdn", "googleusercontent")):
                continue
            entry = found[domain]
            entry["channels"].add(item["feed_id"]); entry["citations"] += 1; entry["example_url"] = entry["example_url"] or url
    rows = [{"domain": domain, "cited_by_channels": len(value["channels"]), "citations": value["citations"], "example_url": value["example_url"]}
            for domain, value in found.items()]
    return sorted(rows, key=lambda row: (-row["cited_by_channels"], -row["citations"], row["domain"]))[:limit]

