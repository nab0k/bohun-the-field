"""One bounded cloud comparison on a fixed set of public-source questions."""
import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from . import providers

MAX_RESULTS = 5
# These are public lookup questions. Expected documents are kept out of search requests.
CASES = (
    ("uk-mod-accounts-25", "What period do the UK MOD annual report and accounts 2024 to 2025 cover?", "gov.uk"),
    ("uk-mod-accounts-24", "Find the UK MOD annual report and accounts 2023 to 2024.", "gov.uk"),
    ("uk-mod-employment", "Find the UK MOD supported employment estimates 2024/25.", "gov.uk"),
    ("uk-veterans-apology", "When did the UK government publish its apology to LGBT veterans?", "gov.uk"),
    ("uk-veterans-response", "Find the UK government response to the LGBT veterans independent review.", "gov.uk"),
    ("uk-veterans-scheme", "Find the official LGBT veterans financial recognition scheme guidance.", "gov.uk"),
    ("uk-veterans-welfare", "Find the independent review of UK government welfare services for veterans.", "gov.uk"),
    ("uk-prosperity-review", "Who authored Growing the contribution of defence to UK prosperity?", "gov.uk"),
    ("us-health-fund", "Find the FY 2025 MERHCF agency financial report.", "health.mil"),
    ("eurostat-cofog", "Find Eurostat government expenditure by function COFOG dataset gov_10a_exp.", "ec.europa.eu"),
    ("sipri-expenditure", "Find the SIPRI military expenditure database release covering 2024.", "sipri.org"),
    ("nao-accounts", "Find the UK NAO Whole of Government Accounts 2023-24 audit report.", "nao.org.uk"),
)


def exa_lite(job):
    return providers._post(
        "https://api.exa.ai/search",
        {"query": job["query"], "type": "lite", "numResults": job["max_results"]},
        {"x-api-key": providers._key("EXA.txt")},
        timeout=20,
    )


def official_candidates(response, domain):
    if not isinstance(response, dict) or not isinstance(response.get("results"), list):
        raise ValueError("invalid search response")
    candidates = []
    for row in response["results"][:MAX_RESULTS]:
        if not isinstance(row, dict) or not isinstance(row.get("url"), str):
            continue
        url = row["url"]
        try:
            parsed = urlsplit(url)
            host = parsed.hostname or ""
        except ValueError:
            continue
        if parsed.scheme == "https" and not parsed.username and not parsed.password and (
            host == domain or host.endswith("." + domain)
        ):
            candidates.append(url)
    return list(dict.fromkeys(candidates))


def compare(skip=(), routes=None):
    routes = routes if routes is not None else {"tavily": providers.tavily_api, "exa": exa_lite}
    result = []
    for case_id, query, domain in CASES:
        row = {"case": case_id, "fact": query}
        if case_id in skip:
            row["status"] = "CORPUS_COVERED"
        else:
            row["providers"] = {}
            for name, search in routes.items():
                try:
                    response = search({"query": query, "max_results": MAX_RESULTS})
                    urls = official_candidates(response, domain)
                    row["providers"][name] = {
                        "status": "NEEDS_SOURCE_REVIEW" if urls else "NO_OFFICIAL_CANDIDATE", "urls": urls,
                    }
                except HTTPError as error:
                    row["providers"][name] = {"status": f"HTTP_{error.code}"}
                except (URLError, TimeoutError, RuntimeError, ValueError):
                    row["providers"][name] = {"status": "UNAVAILABLE"}
        result.append(row)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-covered", default="", help="Comma-separated case IDs verified before dispatch")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    skip = set(filter(None, map(str.strip, args.corpus_covered.split(","))))
    if not skip.issubset({case[0] for case in CASES}):
        parser.error("unknown corpus case ID")
    args.out.write_text(json.dumps(compare(skip), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
