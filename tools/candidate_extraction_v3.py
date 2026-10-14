#!/usr/bin/env python3
"""Extract organization leads from directory cards and JSON-LD, without fetching leads."""
import argparse
import csv
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

ORG_TYPES = {"Organization", "Corporation", "LocalBusiness"}
MEDIA = re.compile(r"media|press|journal|news|publisher|magazine", re.I)
INVESTOR = re.compile(r"investor|venture|capital|fund|portfolio", re.I)
ASSOCIATION = re.compile(r"association|federation|chamber|industry group", re.I)
EXHIBITOR = re.compile(r"exhibitor|exposant|aussteller|company|companies|member|supplier|startup|partner", re.I)
CARD = re.compile(r"exhibitor|company|member|supplier|partner|startup|profile|listing|result|card", re.I)
SOCIAL = ("linkedin.com", "facebook.com", "instagram.com", "youtube.com", "twitter.com", "x.com", "tiktok.com")
FIELDS = ["name", "website", "domain", "category_guess", "evidence_type", "source_page", "confidence", "review_status"]

def domain(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")

def valid_external(url, page):
    p = urlsplit(url)
    host = domain(url)
    return p.scheme in ("http", "https") and bool(host) and host != domain(page) and not any(host == s or host.endswith("." + s) for s in SOCIAL)

def category(text, page):
    text = text + " " + page
    if MEDIA.search(text):
        return "media"
    if INVESTOR.search(text):
        return "investor"
    if ASSOCIATION.search(text):
        return "association"
    if EXHIBITOR.search(text):
        return "industry_organization"
    return "unknown"

def jsonld_items(soup):
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            obj = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        stack = [obj]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node:
                    stack.append(node["@graph"])
                if "itemListElement" in node:
                    stack.append(node["itemListElement"])
                if "item" in node and isinstance(node["item"], (dict, list)):
                    stack.append(node["item"])
                types = node.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if any(t in ORG_TYPES for t in types):
                    yield node

def extract(html, page):
    soup = BeautifulSoup(html, "html.parser")
    found = {}
    def add(name, url, evidence):
        url = urljoin(page, url or "")
        name = re.sub(r"\s+", " ", name or "").strip()[:200]
        if not name or not valid_external(url, page):
            return
        key = domain(url)
        row = {"name": name, "website": url, "domain": key,
               "category_guess": category(name, page), "evidence_type": evidence,
               "source_page": page, "confidence": "medium" if evidence == "jsonld" else "low",
               "review_status": "unverified_candidate"}
        if key not in found or evidence == "jsonld":
            found[key] = row
    for obj in jsonld_items(soup):
        add(obj.get("name"), obj.get("url") or obj.get("sameAs", [None])[0] if isinstance(obj.get("sameAs"), list) else obj.get("url") or obj.get("sameAs"), "jsonld")
    for node in soup.select("article, li, div, tr, section"):
        classes = " ".join(node.get("class", [])) + " " + str(node.get("id", ""))
        if not CARD.search(classes):
            continue
        title = node.select_one("h2, h3, h4, [itemprop=name], .company-name, .exhibitor-name")
        if title is None:
            continue
        name = title.get_text(" ", strip=True)
        for a in node.select("a[href]")[:20]:
            url = urljoin(page, a["href"])
            if valid_external(url, page):
                add(name, url, "directory_card")
                break
    return list(found.values())

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--manifest", required=True, help="CSV: page_url,html_file")
    p.add_argument("--output", default="output/candidate-extraction-v3")
    args = p.parse_args()
    rows, audit = {}, []
    with open(args.manifest, newline="", encoding="utf-8") as fh:
        for entry in csv.DictReader(fh):
            page = entry["page_url"]
            path = Path(entry["html_file"])
            if not path.is_file():
                audit.append({"page": page, "status": "missing_html", "leads": 0})
                continue
            found = extract(path.read_text(encoding="utf-8"), page)
            for item in found:
                rows.setdefault(item["domain"], item)
            audit.append({"page": page, "status": "parsed", "leads": len(found)})
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "candidates.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sorted(rows.values(), key=lambda x: x["domain"]))
    (out / "report.json").write_text(json.dumps({"pages": len(audit), "unique_unverified_candidates": len(rows), "audit": audit, "production_writes": 0}, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
