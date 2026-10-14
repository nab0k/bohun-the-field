#!/usr/bin/env python3
"""Directory discovery v2: bounded, auditable, pagination-aware organisation leads."""
import argparse
import csv
import ipaddress
import json
import re
import socket
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
import requests
from bs4 import BeautifulSoup

UA = "BOHUN-Candidate-Discovery/2.0 (public directory research)"
DIRECTORY = re.compile(r"exhibitor|member|supplier|company|companies|portfolio|startup|directory|participants|partners|members|aussteller|exposant", re.I)
PAGINATION = re.compile(r"^(next|older|[0-9]{1,3}|›|»)$", re.I)
SOCIAL = ("linkedin.com", "facebook.com", "instagram.com", "youtube.com", "x.com", "twitter.com", "tiktok.com", "wikipedia.org", "google.com")
LIMIT = 1_500_000

def host(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")

def normalize(url):
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
        return ""
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))

def public_host(url):
    name = host(url)
    if not name or name in ("localhost",) or name.endswith((".local", ".internal", ".test")):
        return False
    try:
        addresses = socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(item[4][0]).is_global for item in addresses)
    except (socket.gaierror, ValueError):
        return False

def fetch(session, url):
    if not public_host(url):
        return None
    try:
        with session.get(url, timeout=(4, 10), stream=True, allow_redirects=False) as response:
            if response.status_code != 200 or int(response.headers.get("Content-Length", "0")) > LIMIT:
                return None
            data = bytearray()
            for chunk in response.iter_content(32768):
                data.extend(chunk)
                if len(data) > LIMIT:
                    return None
            return BeautifulSoup(bytes(data), "html.parser")
    except (requests.RequestException, ValueError):
        return None

def allowed(session, url, robots):
    root = urlsplit(url).scheme + "://" + urlsplit(url).netloc
    if root not in robots:
        # Fail closed when robots.txt cannot be read.
        if not public_host(root + "/robots.txt"):
            robots[root] = None
        else:
            try:
                r = session.get(root + "/robots.txt", timeout=(4, 8), allow_redirects=False)
                if r.status_code != 200:
                    robots[root] = None
                else:
                    parser = RobotFileParser()
                    parser.parse(r.text[:200000].splitlines())
                    robots[root] = parser
            except requests.RequestException:
                robots[root] = None
    return robots[root] is not None and robots[root].can_fetch(UA, url)

def run(catalog, output, max_pages, max_depth, delay):
    session = requests.Session()
    session.headers["User-Agent"] = UA
    robots = {}
    leads, audit = {}, []
    with open(catalog, newline="", encoding="utf-8") as fh:
        seeds = list(csv.DictReader(fh))
    for seed in seeds:
        start = normalize(seed["directory_url"].strip())
        category = seed["directory_type"].strip()
        if not start:
            continue
        root = host(start)
        queue = deque([(start, 0)])
        visited = set()
        while queue and len(visited) < max_pages:
            url, depth = queue.popleft()
            if url in visited or host(url) != root:
                continue
            visited.add(url)
            if not allowed(session, url, robots):
                audit.append({"seed": start, "page": url, "status": "robots_disallowed", "leads": 0})
                continue
            soup = fetch(session, url)
            if soup is None:
                audit.append({"seed": start, "page": url, "status": "unavailable", "leads": 0})
                continue
            count = 0
            for anchor in soup.select("a[href]")[:5000]:
                target = normalize(urljoin(url, anchor.get("href", "")))
                if not target:
                    continue
                label = anchor.get_text(" ", strip=True)[:200]
                parent = anchor.find_parent(["article", "li", "tr", "div"])
                context = parent.get_text(" ", strip=True)[:350] if parent else label
                if host(target) == root:
                    if depth < max_depth and target not in visited:
                        path = urlsplit(target).path
                        if DIRECTORY.search(path + " " + label) or PAGINATION.match(label) or ("page=" in target and DIRECTORY.search(url)):
                            queue.append((target, depth + 1))
                    continue
                if host(target) in SOCIAL or any(host(target).endswith("." + s) for s in SOCIAL):
                    continue
                # Directory-context evidence required: this is a lead, never a verified company.
                evidence = (label + " " + context).lower()
                if not (DIRECTORY.search(evidence) or DIRECTORY.search(url)):
                    continue
                key = host(target)
                if key not in leads:
                    leads[key] = {"domain": key, "website": target, "name_hint": label,
                                  "directory_page": url, "directory_seed": start,
                                  "directory_type": category, "evidence": context,
                                  "review_status": "unverified_candidate"}
                    count += 1
            audit.append({"seed": start, "page": url, "status": "scanned", "leads": count})
            print(json.dumps(audit[-1]), flush=True)
            time.sleep(delay)
    output.mkdir(parents=True, exist_ok=True)
    def write(name, fields, rows):
        with (output / name).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    write("candidates.csv", ["domain", "website", "name_hint", "directory_page", "directory_seed",
                              "directory_type", "evidence", "review_status"], sorted(leads.values(), key=lambda r: r["domain"]))
    write("page-audit.csv", ["seed", "page", "status", "leads"], audit)
    report = {"seed_directories": len(seeds), "pages_visited": len(audit),
              "pages_scanned": sum(x["status"] == "scanned" for x in audit),
              "unique_unverified_domains": len(leads), "production_writes": 0,
              "inoreader_subscriptions_added": 0}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--catalog", default="data/sources/discovery-directories.csv")
    p.add_argument("--output", type=Path, default=Path("output/candidate-discovery-v2"))
    p.add_argument("--max-pages-per-seed", type=int, default=30)
    p.add_argument("--max-depth", type=int, default=3)
    p.add_argument("--delay", type=float, default=0.5)
    a = p.parse_args()
    run(a.catalog, a.output, a.max_pages_per_seed, a.max_depth, a.delay)
