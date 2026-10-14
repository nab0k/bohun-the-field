"""Fetch a bounded metadata-only feed backlog."""

import datetime as dt
import gzip
import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.parse import quote

import feedparser
from bs4 import BeautifulSoup

from . import read_csv, write_csv
from .canonical import canonical_url, stable_id
from .politeness import PoliteSession

COVERAGE_FIELDS = ["feed_id", "backlog_coverage_days", "items", "undated_items", "error", "history_source"]
ITEM_FIELDS = ["item_id", "feed_id", "url", "canonical_url", "title", "published_at", "fetched_at", "author", "summary_len", "summary_sha256", "categories", "summary_links"]


def entry_date(entry):
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    if stamp:
        return dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc)
    raw = entry.get("published") or entry.get("updated")
    try:
        parsed = parsedate_to_datetime(raw) if raw else None
        return parsed.astimezone(dt.timezone.utc) if parsed and parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc) if parsed else None
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            return parsed.astimezone(dt.timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            return None


def normalize_entry(entry, feed_id, fetched_at):
    url = entry.get("link") or ""
    canonical = canonical_url(url)
    summary = str(entry.get("summary") or entry.get("description") or "")
    soup = BeautifulSoup(summary, "html.parser") if summary else None
    links = sorted({canonical_url(node.get("href")) for node in soup.select("a[href]") if canonical_url(node.get("href"))}) if soup else []
    published = entry_date(entry)
    categories = sorted({tag.get("term", "") for tag in entry.get("tags", []) if isinstance(tag, dict) and tag.get("term")})
    return {
        "item_id": stable_id("item_", canonical or entry.get("id") or entry.get("title")),
        "feed_id": feed_id, "url": url, "canonical_url": canonical,
        "title": str(entry.get("title") or "").strip(),
        "published_at": published.isoformat() if published else "", "fetched_at": fetched_at.isoformat(),
        "author": str(entry.get("author") or "")[:500], "summary_len": len(summary),
        "summary_sha256": hashlib.sha256(summary.encode("utf-8")).hexdigest() if summary else "",
        "categories": "|".join(categories), "summary_links": json.dumps(links, ensure_ascii=False),
    }


def _paged_url(url, page):
    if page == 1:
        return url
    parsed = urlsplit(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["paged"] = str(page)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), ""))


def _news_items(content):
    soup = BeautifulSoup(content, "xml")
    items = []
    for node in soup.find_all("url"):
        loc = node.find("loc")
        title = node.find(lambda tag: tag.name and tag.name.endswith("title"))
        date = node.find(lambda tag: tag.name and tag.name.endswith("publication_date"))
        if loc and title:
            items.append({"link": loc.text.strip(), "title": title.text.strip(), "published": date.text.strip() if date else ""})
    return items


def fetch_feed(feed, client, *, days_back=45, max_pages=10, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days_back)
    items, seen = [], set()
    next_url = feed["feed_url"]
    for page in range(1, max_pages + 1):
        current_url = next_url if page == 1 or next_url != feed["feed_url"] else _paged_url(feed["feed_url"], page)
        # One-shot harvest: never send If-None-Match/If-Modified-Since. Discovery fetched the same feed minutes
        # earlier, so a conditional request returns 304 and the channel would wrongly look empty.
        # ETag/Last-Modified belong to the future poller state, not to this backlog.
        response = client.get(current_url)
        if response.status_code == 304:
            break
        response.raise_for_status()
        parsed = None if feed.get("format") == "news-sitemap" else feedparser.parse(response.content)
        entries = _news_items(response.content) if parsed is None else parsed.entries
        if not entries:
            break
        oldest = None
        added = 0
        for entry in entries:
            row = normalize_entry(entry, feed["feed_id"], now)
            if row["item_id"] in seen:
                continue
            seen.add(row["item_id"])
            date = entry_date(entry)
            if date and date < cutoff:
                oldest = date if oldest is None or date < oldest else oldest
                continue
            items.append(row)
            added += 1
            if date:
                oldest = date if oldest is None or date < oldest else oldest
        if oldest and oldest < cutoff or added == 0 or page == 1 and len(entries) < 10:
            break
        atom_next = next((link.get("href") for link in (parsed.feed.get("links", []) if parsed else []) if link.get("rel") == "next" and link.get("href")), "")
        next_url = atom_next or feed["feed_url"]
    dates = [dt.datetime.fromisoformat(row["published_at"]) for row in items if row["published_at"]]
    coverage = max(0.0, (now - min(dates)).total_seconds() / 86400) if dates else 0.0
    return items, {"feed_id": feed["feed_id"], "backlog_coverage_days": round(coverage, 2), "items": len(items), "undated_items": sum(not row["published_at"] for row in items)}


def run(input_csv, output_dir, *, days_back=45, max_pages=10, client=None, now=None, workers=12):
    feeds = read_csv(input_csv)
    client = client or PoliteSession()
    workers = max(1, min(int(workers), 12))
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_feed, feed, client, days_back=days_back, max_pages=max_pages, now=now): index for index, feed in enumerate(feeds)}
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                feed = feeds[index]
                results[index] = ([], {"feed_id": feed["feed_id"], "backlog_coverage_days": 0, "items": 0, "undated_items": 0, "error": type(exc).__name__})
    items, coverage = [], []
    for index in range(len(feeds)):
        feed_items, feed_coverage = results[index]
        items.extend(feed_items); coverage.append(feed_coverage)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    with gzip.open(output / "items.csv.gz", "wt", newline="", encoding="utf-8") as handle:
        import csv
        writer = csv.DictWriter(handle, fieldnames=ITEM_FIELDS)
        writer.writeheader(); writer.writerows(items)
    for row in coverage:
        row.setdefault("history_source", "feed")
    write_csv(output / "backlog-coverage.csv", coverage, COVERAGE_FIELDS)
    return items, coverage, client.request_count


def recompute_coverage(coverage_rows, items, feed_ids, now=None):
    """Recompute visible history for feeds whose backlog was extended from Inoreader."""
    now = now or dt.datetime.now(dt.timezone.utc)
    by_feed = {}
    for row in items:
        if row["feed_id"] in feed_ids:
            by_feed.setdefault(row["feed_id"], []).append(row)
    out = []
    for row in coverage_rows:
        row = dict(row)
        if row["feed_id"] in by_feed:
            rows = by_feed[row["feed_id"]]
            dates = [dt.datetime.fromisoformat(r["published_at"]) for r in rows if r["published_at"]]
            row["backlog_coverage_days"] = round(max(0.0, (now - min(dates)).total_seconds() / 86400), 2) if dates else 0.0
            row["items"] = len(rows)
            row["undated_items"] = sum(not r["published_at"] for r in rows)
            row["history_source"] = "feed+inoreader"
        out.append(row)
    return out


def inoreader_history(feeds, coverage_rows, candidates, existing_items, *, call_budget, calls_used=0, now=None, days_back=45):
    """Fill short subscribed-feed histories through read-only stream/contents."""
    remaining = max(0, int(call_budget) - int(calls_used))
    if remaining <= 0:
        return [], 0, False
    stream_by_site = {}
    for row in candidates:
        if "I1" in row.get("provenance", "").split("|"):
            stream_by_site.setdefault(row.get("site_id"), row.get("inoreader_stream_id", ""))
    coverage = {row["feed_id"]: float(row.get("backlog_coverage_days") or 0) for row in coverage_rows}
    # shortest visible history first: those gain most from Inoreader's stored history
    targets = sorted((row for row in feeds if row.get("site_id") in stream_by_site and coverage.get(row["feed_id"], 0) < 14),
                     key=lambda row: (coverage.get(row["feed_id"], 0), row["feed_id"]))
    if not targets:
        return [], 0, False
    sys.path.insert(0, str(Path("collectors/inoreader").resolve()))
    from sync import access_token, api
    token = access_token()
    seen = {row["item_id"] for row in existing_items}
    now = now or dt.datetime.now(dt.timezone.utc)
    added, calls, blocked = [], 0, False
    for feed in targets[:remaining]:
        try:
            # Inoreader keys history by the subscription's own feed id, which may differ from our canonical URL
            stream = stream_by_site.get(feed.get("site_id")) or "feed/" + feed["feed_url"]
            payload = api("stream/contents/" + quote(stream, safe="/"), token, {"n": 100, "ot": int((now - dt.timedelta(days=days_back)).timestamp())})
            calls += 1
        except RuntimeError as exc:
            if "status=429" in str(exc):
                print("::notice::INOREADER_QUOTA_BLOCKED — history stopped; public-feed harvest continues")
                blocked = True
                break
            calls += 1  # one feed failing (e.g. 404 for an unknown stream) must not stop the harvest
            continue
        for item in payload.get("items", []):
            links = item.get("canonical") or item.get("alternate") or []
            url = next((link.get("href") for link in links if isinstance(link, dict) and link.get("href")), "")
            stamp = item.get("published") or item.get("updated")
            entry = {"id": item.get("id"), "title": item.get("title"), "link": url,
                     "published": dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat() if isinstance(stamp, (int, float)) else "",
                     "author": (item.get("origin") or {}).get("title", "")}
            row = normalize_entry(entry, feed["feed_id"], now)
            if row["item_id"] not in seen:
                seen.add(row["item_id"]); added.append(row)
    return added, calls, blocked
