"""Bounded Harvest-compatible validation, without importing historical packages."""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit

from .policy import Problem, digest, identity_url
from .radars import feed_data, localtag, xml_root

COMMON_PATHS = ("/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml", "/index.xml",
                "/news/rss", "/press/feed")


class Alternates(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.language = ""

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag.lower() == "html":
            self.language = data.get("lang", "")
        if tag.lower() == "link" and "alternate" in data.get("rel", "").lower().split() and \
                data.get("type", "").lower() in ("application/rss+xml", "application/atom+xml"):
            if data.get("href") and len(self.links) < 14:
                self.links.append(data["href"])


def checked_feed(response, candidate, now, *, language_hint=""):
    data = feed_data(response.body, response.url)
    unique = {}
    for entry in data["entries"]:
        try:
            url = identity_url(entry["url"])
        except Problem:
            continue
        unique[url] = {"url": url, "title": entry["title"], "published_epoch": entry["published_epoch"]}
    entries = list(unique.values())[:20]
    if not entries:
        raise Problem("EMPTY_FEED_REVIEW")
    feed_url = identity_url(response.url)
    title = data["title"]
    comment = "/comments/" in urlsplit(feed_url).path.rstrip("/") + "/" or \
        title.lower().startswith(("comments for", "comments on"))
    restricted = any(re.match(r"^(protected\s*:|premium\s*:|захищено\s*:|защищено\s*:)", e["title"], re.I)
                     for e in entries)
    return {"feed_url": feed_url, "publisher_url": "https://" + urlsplit(candidate).netloc + "/",
            "publisher_evidence": "INPUT_ORIGIN_UNVERIFIED",
            "aliases": [candidate] if candidate != feed_url else [],
            "title": title, "language": data["language"] or language_hint,
            "language_evidence": "feed" if data["language"] else "html-lang" if language_hint else "UNKNOWN",
            "format": data["format"], "entries": entries,
            "content_sha256": digest(response.body.hex()), "validated_at": now,
            "access": "RESTRICTED" if restricted else "PUBLIC", "comment_feed": comment,
            "collection_method": "INOREADER", "section": "", "lifecycle": "VALIDATED",
            "existing_registry_ref": None}


def discover(url, http, now):
    start = identity_url(url)
    first = http.request(start, robots=True)
    if first.status != 200:
        raise Problem("SOURCE_HTTP_" + str(first.status))
    try:
        return [checked_feed(first, start, now)]
    except Problem as exc:
        if str(exc) in ("EMPTY_FEED_REVIEW", "XML_DTD", "XML_COMPLEXITY"):
            raise
    ctype = first.headers.get("content-type", "").lower()
    if "json" in ctype:
        raise Problem("API_ADAPTER_REVIEW")
    if b"<urlset" in first.body[:2048] or b"<sitemapindex" in first.body[:2048]:
        raise Problem("WEB_MONITOR_ADAPTER_REVIEW")
    parser = Alternates()
    try:
        parser.feed(first.body.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        raise Problem("HTML_INVALID") from None
    root = "https://" + urlsplit(first.url).netloc
    targets = [urljoin(first.url, link) for link in parser.links] + [root + path for path in COMMON_PATHS]
    results, endpoints = [], set()
    for target in list(dict.fromkeys(targets))[:14]:
        try:
            target = identity_url(target)
            # Cross-host alternate links are unresolved profiles, not silently trusted aliases.
            if urlsplit(target).hostname != urlsplit(first.url).hostname:
                continue
            result = http.request(target, robots=True)
            if result.status != 200:
                continue
            profile = checked_feed(result, target, now, language_hint=parser.language)
            if profile["feed_url"] not in endpoints:
                endpoints.add(profile["feed_url"])
                results.append(profile)
        except Problem as exc:
            if str(exc) in ("HTTP_REQUEST_CAP", "HTTP_BYTE_CAP", "CYCLE_TIME_CAP", "DNS_TIMEOUT"):
                raise
    if not results:
        raise Problem("WEB_MONITOR_ADAPTER_REVIEW")
    return results
