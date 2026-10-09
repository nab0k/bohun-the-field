#!/usr/bin/env python3
"""Conservative public sitemap / HTML discovery. No bypasses, no article bodies."""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.robotparser
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

UA = "BohundefenceWebMonitor/1.0 (+public-source-monitor)"
KEYWORDS = re.compile(r"(news|press|article|release|story|stories|actualit|presse|nachricht|meldung|aktualn|communiqu|innovation|publication|media|nieuws)", re.I)
LIMIT = 30
MAX_BYTES = 2_000_000

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.title = None
        self._title = False
    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "a" and d.get("href"):
            self.links.append((d["href"], d.get("title") or ""))
        if tag == "title":
            self._title = True
    def handle_data(self, data):
        if self._title:
            self.title = (self.title or "") + data.strip()
    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False

def get(url, robots, delay=0.5):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("HTTPS required")
    origin = parsed.scheme + "://" + parsed.netloc
    if origin not in robots:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            rp.read()
            robots[origin] = rp
        except Exception:
            robots[origin] = None
    rp = robots[origin]
    if rp is None or not rp.can_fetch(UA, url):
        raise PermissionError("robots unavailable or disallows URL")
    time.sleep(delay)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,text/xml"})
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.geturl().split(":")[0] != "https":
            raise ValueError("insecure redirect")
        content = r.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise ValueError("response too large")
        return content.decode("utf-8", errors="replace"), r.geturl()

def same_site(url, base):
    a, b = urllib.parse.urlsplit(url), urllib.parse.urlsplit(base)
    return a.scheme == "https" and a.hostname == b.hostname and not a.username and not a.password

def discover(source, robots):
    base = source["url"]
    parsed = urllib.parse.urlsplit(base)
    root = parsed.scheme + "://" + parsed.netloc
    found = {}
    failures = []
    for target in [root + "/sitemap.xml", base]:
        try:
            body, final = get(target, robots)
            if not same_site(final, base):
                raise ValueError("off-domain redirect")
            if body.lstrip().startswith("<?xml") or "<urlset" in body or "<sitemapindex" in body:
                xml = ET.fromstring(body)
                if xml.tag.endswith("sitemapindex"):
                    maps = [el.text.strip() for el in xml.iter() if el.tag.endswith("loc") and el.text]
                    selected = [u for u in maps if same_site(u, base) and KEYWORDS.search(urllib.parse.urlsplit(u).path)][:3]
                    if not selected:
                        selected = [u for u in maps if same_site(u, base)][:2]
                    for child_url in selected:
                        try:
                            child_body, child_final = get(child_url, robots)
                            if not same_site(child_final, base):
                                raise ValueError("off-domain sitemap redirect")
                            child_xml = ET.fromstring(child_body)
                            if child_xml.tag.endswith("urlset"):
                                for loc in child_xml.iter():
                                    if loc.tag.endswith("loc") and loc.text:
                                        u = loc.text.strip().split("#")[0]
                                        if same_site(u, base) and KEYWORDS.search(urllib.parse.urlsplit(u).path):
                                            found[u] = None
                            else:
                                failures.append("nested_sitemap_index_not_supported")
                        except Exception as exc:
                            failures.append(f"child_sitemap:{type(exc).__name__}")
                elif xml.tag.endswith("urlset"):
                    for loc in xml.iter():
                        if loc.tag.endswith("loc") and loc.text:
                            u = loc.text.strip().split("#")[0]
                            if same_site(u, base) and KEYWORDS.search(urllib.parse.urlsplit(u).path):
                                found[u] = None
                else:
                    failures.append("SITEMAP_INDEX_NOT_YET_SUPPORTED")
            else:
                parser = Links()
                parser.feed(body)
                for href, title in parser.links:
                    u = urllib.parse.urljoin(final, href).split("#")[0]
                    if same_site(u, base) and KEYWORDS.search(urllib.parse.urlsplit(u).path):
                        found[u] = title.strip() or None
        except Exception as exc:
            failures.append(f"{urllib.parse.urlsplit(target).path}: {type(exc).__name__}")
    candidates = [{"url":u, "title":t or u.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " ")[:180]} for u,t in list(found.items())[:LIMIT]]
    return candidates, failures

def db_write(source, candidates):
    import psycopg
    from psycopg.types.json import Jsonb
    password = os.environ["SUPABASE_DB_PASSWORD"]
    params = dict(host=os.environ.get("SUPABASE_DB_HOST","aws-1-eu-central-1.pooler.supabase.com"),
                  port=int(os.environ.get("SUPABASE_DB_PORT","5432")),
                  dbname="postgres",user=os.environ.get("SUPABASE_DB_USER","postgres.tecrucawzukxqtchpxrh"),
                  password=password,sslmode="require",connect_timeout=20)
    inserted = 0
    with psycopg.connect(**params) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM intelligence.sources WHERE name=%s ORDER BY id LIMIT 1", ("Web Monitor: "+source["name"],))
                row = cur.fetchone()
                if row:
                    sid = row[0]
                else:
                    cur.execute("INSERT INTO intelligence.sources (name,url,source_type,collection_method) VALUES (%s,%s,%s,%s) RETURNING id",
                                ("Web Monitor: "+source["name"],source["url"],"news","web_monitor"))
                    sid = cur.fetchone()[0]
                for item in candidates:
                    external = hashlib.sha256(item["url"].encode()).hexdigest()
                    cur.execute("""INSERT INTO intelligence.items
                        (source_id,external_id,title,url,item_type,raw_data)
                        VALUES (%s,%s,%s,%s,'news',%s)
                        ON CONFLICT (source_id,external_id) DO NOTHING RETURNING id""",
                        (sid,external,item["title"],item["url"],Jsonb({"discovery_method":"sitemap_html",
                         "verification_status":"unverified_candidate","country":source["country"],
                         "discovered_at":dt.datetime.now(dt.timezone.utc).isoformat()})))
                    inserted += int(cur.fetchone() is not None)
    return inserted

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply",action="store_true",help="Write unverified candidate URLs to Supabase")
    ap.add_argument("--source-index",type=int,default=None,help=argparse.SUPPRESS)
    ap.add_argument("--per-source-timeout",type=int,default=45,help="Hard wall-clock seconds per source (default 45)")
    ap.add_argument("--sources",default=str(Path(__file__).with_name("sources.json")))
    args = ap.parse_args()
    sources = json.loads(Path(args.sources).read_text())
    if args.source_index is not None:
        source = sources[args.source_index]
        if not source.get("enabled"):
            return
        candidates, failures = discover(source, {})
        new = db_write(source, candidates) if args.apply and candidates else 0
        print(json.dumps({"name": source["name"], "candidates": len(candidates),
                          "inserted": new, "warnings": failures}, ensure_ascii=False), flush=True)
        return

    enabled = [(i, src) for i, src in enumerate(sources) if src.get("enabled")]
    errors = 0
    total = 0
    found = 0
    for index, source in enabled:
        command = [sys.executable, str(Path(__file__).resolve()), "--sources", args.sources,
                   "--source-index", str(index)]
        if args.apply:
            command.append("--apply")
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=args.per_source_timeout, check=False)
            if proc.returncode != 0:
                errors += 1
                print(f"SOURCE {source['name']}: ERROR=worker_exit_{proc.returncode} "
                      f"detail={proc.stderr[-300:].strip()!r}", flush=True)
                continue
            result = json.loads(proc.stdout.strip().splitlines()[-1])
            count = result["candidates"]
            found += count
            total += result["inserted"]
            print(f"SOURCE {source['name']}: candidates={count} inserted={result['inserted']} "
                  f"warnings={','.join(result['warnings']) or 'none'}", flush=True)
            if not count:
                errors += 1
        except subprocess.TimeoutExpired:
            errors += 1
            print(f"SOURCE {source['name']}: ERROR=source_timeout_{args.per_source_timeout}s", flush=True)
        except (ValueError, KeyError, IndexError) as exc:
            errors += 1
            print(f"SOURCE {source['name']}: ERROR=invalid_worker_result_{type(exc).__name__}", flush=True)
    print(f"SUMMARY sources_checked={len(enabled)} candidates={found} inserted={total} "
          f"sources_without_candidates_or_failed={errors} "
          f"mode={'apply' if args.apply else 'dry-run'}", flush=True)
    if errors and args.apply:
        sys.exit(2)

if __name__ == "__main__":
    main()
