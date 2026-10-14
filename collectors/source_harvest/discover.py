"""RSS/Atom/news-sitemap discovery and validation."""

import datetime as dt
import statistics
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin

import feedparser
from urllib.parse import urlsplit
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from . import read_csv, write_csv
from .canonical import canonical_url, stable_id
from .politeness import PoliteSession
from . import sitemap as sitemap_mod

COMMON_PATHS = (
    "/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml",
    "/news/rss", "/press/feed", "/en/rss", "?format=rss", "/feeds/posts/default",
    "/arc/outboundfeeds/rss/",
)
NEWS_HINTS = ("news", "press", "media", "actualit", "nachrichten")


def _date(entry):
    value = entry.get("published_parsed") or entry.get("updated_parsed")
    if value:
        return dt.datetime(*value[:6], tzinfo=dt.timezone.utc)
    raw = entry.get("published") or entry.get("updated")
    try:
        return parsedate_to_datetime(raw).astimezone(dt.timezone.utc) if raw else None
    except (TypeError, ValueError, OverflowError):
        return None


def _news_sitemap(content):
    soup = BeautifulSoup(content, "xml")
    urls = soup.find_all("url")
    news = [node for node in urls if node.find(lambda tag: tag.name and tag.name.endswith("publication_date"))]
    return news


PLATFORM_HOSTS = {"wordpress.org", "wordpress.com", "wordpress.tv", "learn.wordpress.org", "make.wordpress.org",
                  "developer.wordpress.org", "jobs.wordpress.net", "events.wordpress.org", "blogger.com", "medium.com"}


def _host(url):
    return (urlsplit(url or "").hostname or "").lower().removeprefix("www.")


def _same_site(a, b):
    """Same registrable site, roughly: equal hosts or one is a subdomain of the other."""
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def is_comment_feed(url, title=""):
    path = urlsplit(url or "").path.lower()
    title = (title or "").strip().lower()
    return "/comments/" in path + "/" or path.rstrip("/").endswith("/comments") or title.startswith(("comments for", "comments on"))


def validate_feed(response, candidate, method):
    content_type = response.headers.get("content-type", "")
    news = _news_sitemap(response.content) if b"publication_date" in response.content[:2000000] else []
    if news:
        dates = []
        for node in news:
            tag = node.find(lambda item: item.name and item.name.endswith("publication_date"))
            if tag:
                try:
                    dates.append(dt.datetime.fromisoformat(tag.text.strip().replace("Z", "+00:00")))
                except ValueError:
                    pass
        return _record(candidate, response.url, "news-sitemap", len(news), dates, "", response, method)
    parsed = feedparser.parse(response.content)
    fatal = bool(parsed.bozo and not parsed.entries and not parsed.feed)
    valid = [entry for entry in parsed.entries if entry.get("title") and (entry.get("link") or entry.get("links"))]
    if fatal or not valid:
        return None
    if _host(response.url) in PLATFORM_HOSTS or is_comment_feed(response.url, parsed.feed.get("title", "")):
        return None  # WordPress comment feeds echo article titles and would inflate yield
    fmt = "atom" if "atom" in (parsed.version or "").lower() or "atom" in content_type else "rss"
    dates = [value for value in (_date(entry) for entry in valid) if value]
    language = parsed.feed.get("language", "")
    return _record(candidate, response.url, fmt, len(valid), dates, language, response, method)


def _record(candidate, final_url, fmt, count, dates, language, response, method):
    dates = sorted(dates)
    intervals = [(b - a).total_seconds() / 3600 for a, b in zip(dates, dates[1:])]
    feed_url = canonical_url(final_url)
    return {
        "feed_id": stable_id("feed_", feed_url), "site_id": candidate["site_id"],
        "feed_url": feed_url, "final_url": response.url, "format": fmt,
        "entries_count": count, "oldest_entry": dates[0].isoformat() if dates else "",
        "newest_entry": dates[-1].isoformat() if dates else "",
        "median_interval_hours": round(statistics.median(intervals), 2) if intervals else "",
        "has_dates": str(bool(dates)).lower(), "language": language or candidate.get("language", ""),
        "etag": response.headers.get("etag", ""), "last_modified": response.headers.get("last-modified", ""),
        "discovery_method": method,
    }


def _candidates_from_html(base, html):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "html.parser")
    found = []
    for node in soup.select("link[rel~=alternate][href]"):
        media_type = (node.get("type") or "").lower()
        if "rss" in media_type or "atom" in media_type:
            found.append((urljoin(base, node["href"]), "html-alternate"))
    sections = []
    base_host = _host(base)
    for node in soup.select("a[href]"):
        text = (node.get_text(" ", strip=True) + " " + node.get("href", "")).lower()
        url = urljoin(base, node["href"])
        # only the site's own news sections: a "News" link to wordpress.org made WordPress feeds look like channels
        if any(hint in text for hint in NEWS_HINTS) and _same_site(_host(url), base_host):
            sections.append(url)
    return found, sections[:5]


def discover_one(candidate, client):
    attempts = []
    sections = []
    if candidate.get("feed_url"):
        attempts.append((candidate["feed_url"], "input-feed"))
    site = candidate.get("site_url") or candidate.get("feed_url")
    homepage_status = ""
    if site:
        try:
            homepage = client.get(site)
            homepage_status = str(homepage.status_code)
            if homepage.ok:
                found, sections = _candidates_from_html(homepage.url, homepage.text[:2000000])
                attempts.extend(found)
        except PermissionError:
            homepage_status = "robots_disallowed"
        except Exception as exc:
            homepage_status = type(exc).__name__
        attempts.extend((urljoin(site + "/", suffix.lstrip("/")), "common-path") for suffix in COMMON_PATHS)
        for section in sections:
            try:
                response = client.get(section)
                if response.ok:
                    found, _ = _candidates_from_html(response.url, response.text[:2000000])
                    attempts.extend((url, "section-alternate") for url, _ in found)
            except (PermissionError, Exception):
                continue
        attempts.extend((urljoin(site + "/", suffix), "sitemap") for suffix in ("news-sitemap.xml", "sitemap_news.xml"))
    seen = set()
    tried = []
    for url, method in attempts:
        normalized = canonical_url(url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        tried.append(method)
        try:
            response = client.get(url)
            if response.status_code == 200 and len(response.content) <= 10_000_000:
                record = validate_feed(response, candidate, method)
                if record:
                    return record, None
        except (PermissionError, Exception):
            continue
    # Level-1 fallback: ordinary sitemap.xml with <lastmod> (titles from URL slugs)
    if site and homepage_status not in ("robots_disallowed",):
        try:
            sitemap_url, rows = sitemap_mod.find_sitemap(site, client)
        except Exception:
            sitemap_url, rows = None, []
        if sitemap_url:
            tried.append("sitemap-lastmod")
            dates = [date for _, date in rows]
            return {"feed_id": stable_id("feed_", canonical_url(sitemap_url)), "site_id": candidate["site_id"],
                    "feed_url": canonical_url(sitemap_url), "final_url": sitemap_url, "format": "sitemap",
                    "entries_count": len(rows), "oldest_entry": min(dates).isoformat(), "newest_entry": max(dates).isoformat(),
                    "median_interval_hours": "", "has_dates": "true", "language": "", "etag": "", "last_modified": "",
                    "discovery_method": "sitemap-lastmod"}, None
    return None, {"candidate_id": candidate["candidate_id"], "site_id": candidate["site_id"], "site_url": site,
                  "method_tried": "|".join(sorted(set(tried))), "has_news_section": str(bool(sections)).lower(),
                  "homepage_status": homepage_status or "no_site"}


def run(input_csv, output_dir, *, limit=None, client=None, workers=12, deadline_seconds=None):
    """Discover feeds. Candidates not started before the deadline are recorded as not attempted, not dropped."""
    import time as _time
    deadline = _time.monotonic() + deadline_seconds if deadline_seconds else None

    def guarded(candidate):
        if deadline and _time.monotonic() > deadline:
            return None, {"candidate_id": candidate["candidate_id"], "site_id": candidate["site_id"],
                          "site_url": candidate.get("site_url", ""), "method_tried": "deadline_not_attempted",
                          "has_news_section": "", "homepage_status": ""}
        return discover_one(candidate, client)

    candidates = read_csv(input_csv)
    if limit:
        candidates = candidates[:limit]
    client = client or PoliteSession()
    workers = max(1, min(int(workers), 12))
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(guarded, candidate): index for index, candidate in enumerate(candidates)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    feeds, pending = [], []
    seen_feeds = set()
    for index in range(len(candidates)):
        feed, miss = results[index]
        if feed and feed["feed_id"] in seen_feeds:
            continue  # two candidates of one site resolved to the same feed: keep one row, or items are counted twice
        if feed:
            seen_feeds.add(feed["feed_id"])
        (feeds if feed else pending).append(feed or miss)
    fields = ["feed_id", "site_id", "feed_url", "final_url", "format", "entries_count", "oldest_entry", "newest_entry", "median_interval_hours", "has_dates", "language", "etag", "last_modified", "discovery_method"]
    output = Path(output_dir)
    write_csv(output / "validated-feeds.csv", feeds, fields)
    write_csv(output / "pending-no-rss.csv", pending, ["candidate_id", "site_id", "site_url", "method_tried", "has_news_section", "homepage_status"])
    return feeds, pending, client.request_count

