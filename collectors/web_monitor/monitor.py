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
from zoneinfo import ZoneInfo

UA = "BohundefenceWebMonitor/1.0 (+public-source-monitor)"
KEYWORDS = re.compile(r"(news|press|article|release|story|stories|actualit|presse|nachricht|meldung|aktualn|communiqu|innovation|publication|media|nieuws)", re.I)
LIMIT = 30
ARTICLE_PATH = re.compile(r"/(?:news|press-releases?|articles?|stories|actualites|communiques|nachrichten|meldungen|pressemitteilungen)/[^/?#]+", re.I)
DATE_PATH = re.compile(r"/20\d{2}/(?:0?[1-9]|1[0-2])/(?:[0-3]?\d)/")
INDEX_PATH = re.compile(r"/(?:news|press|media|publications|innovation|updates|events|topics|tags?|aktuelles|meldungen|pressemitteilungen)/?$", re.I)
ASSET_PATH = re.compile(r"\.(?:pdf|jpg|jpeg|png|svg|zip|docx?|xlsx?|xml|json)$", re.I)

def classify_candidate(url, base):
    """Heuristic only. An article-shaped URL is NOT a verified publication."""
    path = urllib.parse.urlsplit(url).path
    if not same_site(url, base) or ASSET_PATH.search(path):
        return "reject"
    if path in ("", "/") or INDEX_PATH.search(path):
        return "index"
    if ARTICLE_PATH.search(path) or DATE_PATH.search(path) or re.search(r"/aktuelles/pressemitteilungen/[^/]+\.html$", path, re.I):
        return "possible_article"
    return "review"

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
    candidates = []
    for u, t in found.items():
        quality = classify_candidate(u, base)
        if quality == "reject":
            continue
        candidates.append({"url": u, "title": (t or u.rstrip("/").split("/")[-1].replace("-", " ").replace("_", " "))[:180], "quality": quality})
        if len(candidates) >= LIMIT:
            break
    return candidates, failures


def discover_channels(source, robots, channels):
    """Discover candidates from explicitly configured publication listings, not global sitemaps."""
    found = {}
    failures = []
    active = [c for c in channels if c.get("enabled") and c.get("source") == source["name"]]
    for channel in active:
        url = channel["url"]
        if not same_site(url, source["url"]):
            failures.append("channel_off_host")
            continue
        try:
            body, final = get(url, robots)
            if not same_site(final, source["url"]):
                raise ValueError("off_host_redirect")
            parser = Links()
            parser.feed(body)
            listing_path = urllib.parse.urlsplit(final).path.rstrip("/")
            link_stats = {"total": len(parser.links), "same_host": 0, "article_shaped": 0, "selected": 0}
            for href, label in parser.links:
                candidate = urllib.parse.urljoin(final, href).split("#")[0]
                if not same_site(candidate, source["url"]):
                    continue
                link_stats["same_host"] += 1
                path = urllib.parse.urlsplit(candidate).path.rstrip("/")
                # A candidate must be below the listing or match an explicit
                # article URL shape. A section page is never an article.
                under_listing = path.startswith(listing_path + "/")
                article_shape = bool(ARTICLE_PATH.search(path) or DATE_PATH.search(path) or (source["name"] == "BDSV" and re.search(r"/aktuelles/pressemitteilungen/[^/]+\.html$", path, re.I)))
                if article_shape:
                    link_stats["article_shaped"] += 1
                if not (under_listing or article_shape):
                    continue
                if path == listing_path or ASSET_PATH.search(path):
                    continue
                quality = classify_candidate(candidate, source["url"])
                if quality in ("reject", "index"):
                    continue
                link_stats["selected"] += 1
                found[candidate] = {"url": candidate,
                                    "title": (label.strip() or path.rsplit("/", 1)[-1].replace("-", " "))[:180],
                                    "quality": quality,
                                    "channel_url": url}
                if len(found) >= LIMIT:
                    break
            print("CHANNEL_DIAGNOSTIC " + json.dumps({"source": source["name"], "url": url, **link_stats}), flush=True)
        except PermissionError:
            failures.append("channel:robots_unavailable_or_disallowed")
        except Exception as exc:
            failures.append("channel:" + type(exc).__name__)
        if len(found) >= LIMIT:
            break
    return list(found.values()), failures

class ArticleMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.canonical = None
        self.title = ""
        self.in_title = False
        self.json_ld = []
        self.in_json_ld = False
        self.date_elements = []
        self.date_depth = 0
        self.press_dateline = None
        self._body_text = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.date_depth:
            self.date_depth += 1
        elif tag == "time" or (tag in ("span", "div", "p") and re.search(r"(?:^|[\s_-])(?:date|datum|published)(?:$|[\s_-])", a.get("class", ""), re.I)):
            self.date_elements.append({"datetime": a.get("datetime"), "text": ""})
            self.date_depth = 1
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta[key] = a["content"].strip()
        if tag == "link" and "canonical" in a.get("rel", "").lower().split():
            self.canonical = a.get("href")
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.in_json_ld = True
            self.json_ld.append("")
        if tag == "title":
            self.in_title = True
    def handle_data(self, data):
        if self.in_json_ld:
            self.json_ld[-1] += data
        if self.in_title:
            self.title += data
        if self.date_depth and self.date_elements:
            self.date_elements[-1]['text'] += data
        self._body_text.append(data)
    def handle_endtag(self, tag):
        if self.date_depth:
            self.date_depth -= 1
        if tag == "script":
            self.in_json_ld = False
        if tag == "title":
            self.in_title = False

GERMAN_MONTHS = {"januar":1,"februar":2,"märz":3,"maerz":3,"april":4,"mai":5,"juni":6,"juli":7,"august":8,"september":9,"oktober":10,"november":11,"dezember":12}

def normalize_german_date(raw):
    if not isinstance(raw, str):
        return None
    m = re.search(r"\b([0-3]?\d)\.\s*([A-Za-zÄÖÜäöüß]+)\s+(20\d{2})\b", raw)
    if not m or m.group(2).lower() not in GERMAN_MONTHS:
        return None
    try:
        return dt.datetime(int(m.group(3)), GERMAN_MONTHS[m.group(2).lower()], int(m.group(1)), tzinfo=ZoneInfo("Europe/Berlin")).isoformat()
    except ValueError:
        return None

def audit_candidate(item, base, robots):
    """Inspect public HTML metadata; fail closed on ambiguity and access errors."""
    result = {"url": item["url"], "classification": item["quality"], "verified": False}
    if item["quality"] == "index":
        result["reason"] = "section_index_not_audited"
        return result
    try:
        body, final = get(item["url"], robots)
        if not same_site(final, base):
            raise ValueError("off_domain_redirect")
        parser = ArticleMetadata()
        parser.feed(body)
        structured = {}
        for raw in parser.json_ld:
            try:
                obj = json.loads(raw)
                nodes = obj if isinstance(obj, list) else [obj]
                for node in nodes:
                    if isinstance(node, dict):
                        nodes_to_check = node.get("@graph", [node])
                        for candidate in nodes_to_check:
                            if not isinstance(candidate, dict):
                                continue
                            types = candidate.get("@type", [])
                            if isinstance(types, str):
                                types = [types]
                            if any(t in ("NewsArticle", "Article", "PressRelease") for t in types):
                                structured = candidate
                                break
                    if structured:
                        break
            except (ValueError, TypeError):
                continue
            if structured:
                break
        title = parser.meta.get("og:title") or parser.meta.get("twitter:title") or structured.get("headline") or parser.title.strip()
        date = next((parser.meta[k] for k in ("article:published_time", "datepublished", "date", "dc.date.issued", "pubdate") if parser.meta.get(k)), None) or structured.get("datePublished")
        date_precision = None
        if not date:
            for el in parser.date_elements:
                raw = el["datetime"] or el["text"].strip()
                normalized = normalize_german_date(raw)
                if normalized:
                    date, date_precision = normalized, "day"
                    break
                if raw and re.match(r"^20\d{2}-\d{2}-\d{2}T", raw):
                    date = raw
                    break
        if not date and urllib.parse.urlsplit(final).hostname in ("bdsv.eu", "www.bdsv.eu"):
            # BDSV press releases expose a formal Berlin dateline in the body.
            # Never infer publication time from arbitrary dates elsewhere.
            body_text = " ".join(parser._body_text)
            dateline = re.search(r"Berlin,?\s+den\s+([0-3]?\d\.[01]?\d\.20\d{2})\b", body_text, re.I)
            if dateline:
                try:
                    day = dt.datetime.strptime(dateline.group(1), "%d.%m.%Y")
                    date = day.replace(tzinfo=ZoneInfo("Europe/Berlin")).isoformat()
                    date_precision = "day"
                except ValueError:
                    pass
        canonical = urllib.parse.urljoin(final, parser.canonical) if parser.canonical else final
        if not same_site(canonical, base):
            raise ValueError("off_domain_canonical")
        result.update({"title": title[:240], "published_at_raw": date, "canonical_url": canonical})
        # Reject section and directory URLs even when they expose dates in metadata.
        if classify_candidate(canonical, base) != "possible_article":
            result["reason"] = "not_article_url"
            return result
        if not title or not date:
            result["reason"] = "missing_title_or_publication_date"
        else:
            try:
                normalized = dt.datetime.fromisoformat(date.replace("Z", "+00:00"))
                if normalized.tzinfo is None:
                    result["reason"] = "date_without_timezone"
                elif normalized > dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=1):
                    result["reason"] = "future_publication_date"
                else:
                    result["verified"] = True
                    if date_precision:
                        result["date_precision"] = date_precision
                    result["reason"] = "metadata_title_date_canonical_present"
                    result["published_at"] = normalized.isoformat()
            except ValueError:
                result["reason"] = "unparseable_publication_date"
    except Exception as exc:
        result["reason"] = "fetch_" + type(exc).__name__
    return result

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
    ap.add_argument("--report-candidates",action="store_true",help="Show classified candidate URLs")
    ap.add_argument("--audit-metadata",action="store_true",help="Inspect all article-shaped or review candidates per source")
    ap.add_argument("--source-index",type=int,default=None,help=argparse.SUPPRESS)
    ap.add_argument("--per-source-timeout",type=int,default=45,help="Hard wall-clock seconds per source (default 45)")
    ap.add_argument("--country",default=None,help="Limit dry-run discovery to ISO country code, e.g. DE")
    ap.add_argument("--sources",default=str(Path(__file__).with_name("sources.json")))
    ap.add_argument("--channels",default=str(Path(__file__).with_name("channels.json")))
    ap.add_argument("--discovery-mode",choices=("channels","legacy"),default="channels")
    args = ap.parse_args()
    if args.audit_metadata and args.per_source_timeout < 90:
        args.per_source_timeout = 90
    if args.apply:
        ap.error("--apply is disabled until verified article extraction is implemented")
    sources = json.loads(Path(args.sources).read_text())
    if args.source_index is not None:
        source = sources[args.source_index]
        if not source.get("enabled"):
            return
        channels = json.loads(Path(args.channels).read_text())
        if args.discovery_mode == "channels":
            candidates, failures = discover_channels(source, {}, channels)
        else:
            candidates, failures = discover(source, {})
        new = 0
        if args.audit_metadata:
            robots = {}
            checked = 0
            for item in candidates:
                if item["quality"] in ("possible_article", "review") :
                    print("AUDIT " + json.dumps({"source": source["name"], **audit_candidate(item, source["url"], robots)}, ensure_ascii=False), flush=True)
                    checked += 1
        if args.report_candidates:
            for item in candidates:
                print("CANDIDATE " + json.dumps({"source": source["name"], **item}, ensure_ascii=False), flush=True)
        print(json.dumps({"name": source["name"], "candidates": len(candidates),
                          "inserted": new, "warnings": failures, "quality": {k: sum(x["quality"] == k for x in candidates) for k in ("possible_article", "review", "index")}}, ensure_ascii=False), flush=True)
        return

    enabled = [(i, src) for i, src in enumerate(sources) if src.get("enabled") and (not args.country or src.get("country") == args.country)]
    errors = 0
    total = 0
    found = 0
    verified = 0
    audited = 0
    for index, source in enabled:
        command = [sys.executable, str(Path(__file__).resolve()), "--sources", args.sources,
                   "--source-index", str(index), "--channels", args.channels,
                   "--discovery-mode", args.discovery_mode]
        if args.report_candidates:
            command.append("--report-candidates")
        if args.audit_metadata:
            command.append("--audit-metadata")
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  timeout=args.per_source_timeout, check=False)
            if proc.returncode != 0:
                errors += 1
                print(f"SOURCE {source['name']}: ERROR=worker_exit_{proc.returncode} "
                      f"detail={proc.stderr[-300:].strip()!r}", flush=True)
                continue
            if args.report_candidates:
                for line in proc.stdout.splitlines():
                    if line.startswith("CANDIDATE "):
                        print(line, flush=True)
            if args.audit_metadata:
                for line in proc.stdout.splitlines():
                    if line.startswith("AUDIT "):
                        print(line, flush=True)
                        audited += 1
                        try:
                            verified += int(json.loads(line[6:]).get("verified") is True)
                        except ValueError:
                            pass
            result = json.loads(proc.stdout.strip().splitlines()[-1])
            count = result["candidates"]
            found += count
            total += result["inserted"]
            print(f"SOURCE {source['name']}: candidates={count} inserted={result['inserted']} "
                  f"quality={result.get('quality', {})} warnings={','.join(result['warnings']) or 'none'}", flush=True)
            if not count:
                errors += 1
        except subprocess.TimeoutExpired:
            errors += 1
            print(f"SOURCE {source['name']}: ERROR=source_timeout_{args.per_source_timeout}s", flush=True)
        except (ValueError, KeyError, IndexError) as exc:
            errors += 1
            print(f"SOURCE {source['name']}: ERROR=invalid_worker_result_{type(exc).__name__}", flush=True)
    print(f"SUMMARY sources_checked={len(enabled)} candidates={found} inserted={total} "
          f"sources_without_candidates_or_failed={errors} audited={audited} verified={verified} "
          f"mode={'apply' if args.apply else 'dry-run'} discovery={args.discovery_mode}", flush=True)
    # Distinguish successful execution from healthy source coverage in CI.
    # No writes are performed. A failed/empty source must not yield a green run.
    if errors:
        sys.exit(2)

if __name__ == "__main__":
    main()
