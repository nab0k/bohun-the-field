#!/usr/bin/env python3
"""Merge curated Top100 with additional country evidence leads for discovery (no writes)."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

def host(url):
    return (urlsplit(url or "").hostname or "").lower().removeprefix("www.")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="output/source-discovery-expanded.json")
    args = p.parse_args()
    top = json.loads(Path("collectors/inoreader/top100_sources.json").read_text(encoding="utf-8"))["sources"]
    extra = json.loads(Path("collectors/inoreader/country_candidate_extensions_batch01.json").read_text(encoding="utf-8"))["sources"]
    combined, known = [], set()
    for candidate in top + extra:
        h = host(candidate["url"])
        if not h or h in known:
            continue
        known.add(h)
        record = dict(candidate)
        if "discovered_in" in record:
            # Company profile/article URLs were discovery evidence, not feed landing pages.
            record["example_evidence_url"] = record["url"]
            record["url"] = "https://" + urlsplit(record["url"]).netloc + "/"
        combined.append(record)
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"source": "Top100 plus country evidence leads", "status": "site_candidates_unverified",
                                   "sources": combined}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"DISCOVERY_REGISTRY baseline={len(top)} country_leads={len(extra)} unique_hosts={len(combined)}")

if __name__ == "__main__":
    main()
