"""Bounded Polish web queries and unverified links from a single provider."""
import json
from urllib.parse import urlsplit

from .policy import Problem, parse_json, require
from .seed_provider import STOP

TAVILY = "https://api.tavily.com/search"
RESULTS_PER_QUERY = 8
QUERY_PREFIXES = (
    "polska stowarzyszenie branżowe aktualności",
    "polska izba branżowa aktualności",
    "polska związek pracodawców komunikaty",
    "polska klaster przemysłowy aktualności",
    "polska federacja przemysłowa aktualności",
    "polska organizacja branżowa forum przemysłowe",
)


def queries(topic):
    return [f"{prefix} {topic}" for prefix in QUERY_PREFIXES]


def search(http, normalize, topic, key):
    audit = {"provider": "tavily", "reason": "empty", "sites": 0, "queries": []}
    if not key:
        audit["reason"] = "TAVILY_KEY_UNAVAILABLE"
        return [], audit

    sites = {}
    for query in queries(topic):
        entry = {"query": query, "status": "ok", "hits": 0}
        audit["queries"].append(entry)
        try:
            result = http.request(
                TAVILY, method="POST", headers={
                    "Authorization": "Bearer " + key,
                    "Content-Type": "application/json",
                }, body=json.dumps({
                    "query": query,
                    "search_depth": "basic",
                    "max_results": RESULTS_PER_QUERY,
                    "include_answer": False,
                    "include_raw_content": False,
                    "auto_parameters": False,
                }).encode(), limit=256_000,
            )
            require(result.status == 200, f"TAVILY_HTTP_{result.status}")
            document = parse_json(result.body, limit=256_000)
            require(isinstance(document, dict) and isinstance(document.get("results"), list),
                    "TAVILY_RESPONSE_INVALID")
        except Problem as exc:
            entry["status"] = audit["reason"] = str(exc)
            break

        for hit in document["results"][:RESULTS_PER_QUERY]:
            if not isinstance(hit, dict):
                continue
            try:
                url = normalize(hit.get("url"))
            except Problem:
                continue
            parts = urlsplit(url)
            host = parts.hostname.removeprefix("www.")
            entry["hits"] += 1
            if host in sites:
                continue
            sites[host] = {
                "url": url,
                "label": host,
                "provider": "tavily",
                "provider_url": TAVILY,
                "query": query,
            }

    audit["sites"] = len(sites)
    if sites and audit["reason"] not in STOP:
        audit["reason"] = "ok" if audit["reason"] == "empty" else "partial"
    return list(sites.values()), audit
