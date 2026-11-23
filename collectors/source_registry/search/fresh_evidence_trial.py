"""Bounded, manual cloud trial of archive, Exa, then Tavily."""
import argparse
from collections import Counter
import json
from pathlib import Path
from time import monotonic
from urllib.error import HTTPError, URLError

from collectors.inoreader.content_contract import visible_text
from collectors.kb_pilot import fulltext, transport
from . import compare_evidence_search as comparison, providers

CASE_COUNT = 12
MAX_READS = 2


def validate(cases):
    if not isinstance(cases, list) or len(cases) != CASE_COUNT:
        raise ValueError("expected 12 public cases")
    keys = {"id", "region", "query", "domain", "quote", "archive"}
    for case in cases:
        if not isinstance(case, dict) or set(case) != keys:
            raise ValueError("invalid case fields")
        for key, size in (("id", 60), ("region", 60), ("query", 280), ("domain", 253), ("quote", 200)):
            value = case[key]
            if not isinstance(value, str) or not 0 < len(value.strip()) <= size:
                raise ValueError("invalid public case value")
        host, path = transport.target("https://" + case["domain"] + "/")
        if host != case["domain"] or path != "/":
            raise ValueError("invalid publisher domain")
        if len(case["quote"].split()) < 6:
            raise ValueError("expected a specific evidence quote")
        archive = case["archive"]
        if not isinstance(archive, list) or len(archive) > 5:
            raise ValueError("invalid archive entries")
        for doc in archive:
            if not isinstance(doc, dict) or set(doc) != {"url", "text"}:
                raise ValueError("invalid archive document")
            if not isinstance(doc["text"], str) or len(doc["text"]) > 1500:
                raise ValueError("invalid archive text")
            if not comparison.official_candidates({"results": [doc]}, case["domain"]):
                raise ValueError("archive domain mismatch")
            transport.target(doc["url"])
    if len({case["id"] for case in cases}) != CASE_COUNT:
        raise ValueError("duplicate case IDs")
    if len({case["region"] for case in cases}) < 3 or len({case["domain"] for case in cases}) < 3:
        raise ValueError("expected at least three regions and three publisher domains")


def excerpt(text, expected):
    text, expected = " ".join(text.split()), " ".join(expected.split())
    offset = text.lower().find(expected.lower())
    return None if offset < 0 else text[offset:offset + len(expected)]


def evidence(url, text, quote):
    passage = excerpt(text, quote)
    return {"url": url, "excerpt": passage} if passage else None


def search_one(case, search, read):
    try:
        response = search({"query": case["query"], "max_results": 5})
        urls = comparison.official_candidates(response, case["domain"])
    except HTTPError as error:
        return {"status": f"HTTP_{error.code}", "reads": 0}
    except (URLError, TimeoutError, RuntimeError, ValueError):
        return {"status": "UNAVAILABLE", "reads": 0}

    reads, failures = 0, 0
    for url in urls[:MAX_READS]:
        reads += 1
        try:
            found = evidence(url, read(url), case["quote"])
        except (OSError, ValueError):
            failures += 1
            continue
        if found:
            return {"status": "QUOTE_MATCH", "reads": reads, "read_failures": failures, "evidence": found}
    status = "NO_OFFICIAL_LINK" if not urls else "EVIDENCE_NOT_FOUND"
    return {"status": status, "reads": reads, "read_failures": failures}


def run(cases, routes, read):
    rows, calls = [], Counter()
    for case in cases:
        row = {"id": case["id"], "status": "WAITING_EVIDENCE", "attempts": {}}
        rows.append(row)
        for doc in case["archive"]:
            found = evidence(doc["url"], doc["text"], case["quote"])
            if found:
                row.update(status="QUOTE_MATCH", source="archive", evidence=found)
                break
        if row["status"] == "QUOTE_MATCH":
            continue
        for name, search in routes:
            calls[name] += 1
            result = search_one(case, search, read)
            row["attempts"][name] = result["status"]
            row.setdefault("reads", {})[name] = result["reads"]
            row.setdefault("read_failures", {})[name] = result.get("read_failures", 0)
            if result["status"] == "QUOTE_MATCH":
                row.update(status="QUOTE_MATCH", source=name, evidence=result["evidence"])
                break
    return {"cases": rows, "search_calls": dict(calls)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    cases = json.loads(args.manifest.read_text(encoding="utf-8"))
    validate(cases)
    robots = fulltext.Robots(transport.fetch)

    def read(url):
        transport.target(url)
        if not robots.allowed(url)[0]:
            raise transport.FetchError("ROBOTS_DENIED")
        page = transport.fetch(url, {url}, limit=1024 * 1024, timeout=10)
        return visible_text(page["body"].decode("utf-8"))

    start = monotonic()
    report = run(cases, (("exa", comparison.exa_lite), ("tavily", providers.tavily_api)), read)
    report["seconds"] = round(monotonic() - start, 2)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"statuses": Counter(row["status"] for row in report["cases"]),
                      "sources": Counter(row["source"] for row in report["cases"] if "source" in row),
                      "search_calls": report["search_calls"], "seconds": report["seconds"]}))


if __name__ == "__main__":
    main()
