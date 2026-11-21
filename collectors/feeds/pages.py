"""A4SC-03 sources without a feed (delivery='page'), approved 21.11 (DESIGN_PAGE_COLLECTOR.md in BOHUN-local EXP-034).

No per-site rules and no URL-shape filter (the Web Monitor lesson of 13.11). Three general ways, in order:
1. a hidden feed: <link rel="alternate" type="application/rss+xml|atom+xml"> of the same site on the page;
2. the site map: Sitemap lines of robots.txt or /sitemap.xml, addresses with a date not older than 7 days;
3. new headline links of the section page: links with headline text, outside nav/header/footer/aside, the largest
   group by path template is the list of articles. The first poll of a page only remembers its links.
New articles are read here (same transport rules), text and date by trafilatura 2.3.1 as in A5TC-02; a link that
gives less than 300 characters is not an article and is remembered without being written. Seen links live in
source_discovery.feed_items_shadow. Writing to intelligence.items needs PAGES_WRITE=true (owner word after shadow).
"""
import datetime as dt
import gzip
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from html.parser import HTMLParser

from collectors.feeds import podcast

COLLECTOR = "a4sc-pages-v1"
FRESH_DAYS = 7
MIN_TEXT = 300
PER_SOURCE = 3
PER_RUN = 60
MIN_LINKS = 3
MAX_SITEMAPS = 3
FEED_TYPES = ("application/rss+xml", "application/atom+xml")
SKIP_TAGS = {"nav", "header", "footer", "aside"}
FILE_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|svg|webp|mp4|mp3|zip|docx?|xlsx?|pptx?|css|js|xml|ico)$", re.I)
# section words that say "news", not a topic: a listing path made only of these does not narrow the site
GENERIC = {"news", "nieuws", "newsroom", "press", "press-releases", "pressreleases", "blog", "blogs", "latest",
           "aktualnosci", "aktualnosci5", "actualites", "noticias", "notizie", "nachrichten", "aktuelles", "media",
           "news-and-media", "news-and-events", "archive", "en", "uk", "de", "fr", "es", "it", "nl", "pl", "ro",
           "index.php", "category", "categories", "section", "tag", "coverage", "web", "searchset", "articles",
           "article", "posts", "stories", "updates", "all", "home", "press-room", "pressroom", "events"}
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
NEWS_NS = "{http://www.google.com/schemas/sitemap-news/0.9}"


class _Page(HTMLParser):
    """Anchors with their text and whether they sit in page chrome; alternate feed links."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links, self.feeds, self.depth, self.cur = [], [], Counter(), None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in SKIP_TAGS:
            self.depth[tag] += 1
        if tag == "link" and "alternate" in (a.get("rel") or "").lower() and (a.get("type") or "").lower() in FEED_TYPES:
            if a.get("href"):
                self.feeds.append(a["href"])
        if tag == "a" and a.get("href"):
            self.cur = [a["href"], [], sum(self.depth.values()) > 0]

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.depth[tag]:
            self.depth[tag] -= 1
        if tag == "a" and self.cur:
            href, text, chrome = self.cur
            self.links.append((href, " ".join(" ".join(text).split()), chrome))
            self.cur = None

    def handle_data(self, data):
        if self.cur is not None:
            self.cur[1].append(data)


def parse_page(body, base):
    p = _Page()
    try:
        p.feed(body.decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 - broken HTML gives what was parsed so far
        pass
    feeds = [urllib.parse.urljoin(base, f) for f in p.feeds]
    return p.links, [f for f in feeds if same_site(f, base)]


def site(url):
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


def same_site(url, base):
    a, b = site(url), site(base)
    return bool(a) and (a == b or a.endswith("." + b) or b.endswith("." + a))


def topic_words(listing):
    """Path words of the listing that name a topic (ainonline.com/section/defense -> {'defense'})."""
    segs = [s.lower() for s in urllib.parse.urlsplit(listing).path.split("/") if s]
    return {s for s in segs if s not in GENERIC and not s.isdigit() and len(s) > 2}


def on_topic(url, words):
    path = urllib.parse.urlsplit(url).path.lower()
    return not words or any(w in path for w in words)


def template(url):
    """Parent path with digits blurred: /news/2026/11/slug -> /news/#/#/ ; /web/obrona-narodowa/slug -> same parent."""
    segs = [s for s in urllib.parse.urlsplit(url).path.split("/") if s]
    return "/" + "/".join(re.sub(r"\d+", "#", s) for s in segs[:-1]) + ("/" if len(segs) > 1 else "")


def headline_links(links, listing):
    """Headline links of the article list: the largest path-template group of same-site headline links."""
    from collectors.feeds.collect import canonical
    own = canonical(listing)
    groups = defaultdict(dict)
    for href, text, chrome in links:
        url = canonical(urllib.parse.urljoin(listing, href))
        if (chrome or not url or url == own or not same_site(url, listing) or FILE_EXT.search(url)
                or urllib.parse.urlsplit(url).path in ("", "/")):
            continue
        if len(text) < 25 and len(text.split()) < 4:
            continue
        groups[template(url)].setdefault(url, text[:500])
    if not groups:
        return []
    best = max(groups.values(), key=len)
    return list(best.items()) if len(best) >= MIN_LINKS else []


def when(text):
    if not text:
        return None
    try:
        d = dt.datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        try:
            d = dt.datetime.strptime(text.strip()[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def sitemap_urls(body):
    """Sitemap bytes -> ("index", [child urls with lastmod]) or ("urls", [(url, date)])."""
    if body[:2] == b"\x1f\x8b":
        try:
            body = gzip.decompress(body)[:4_000_000]
        except OSError:
            return "urls", []
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return "urls", []
    if root.tag.endswith("sitemapindex"):
        return "index", [(s.findtext(f"{SITEMAP_NS}loc") or "", when(s.findtext(f"{SITEMAP_NS}lastmod")))
                         for s in root.iter(f"{SITEMAP_NS}sitemap")]
    out = []
    for u in root.iter(f"{SITEMAP_NS}url"):
        date = when(u.findtext(f"{NEWS_NS}news/{NEWS_NS}publication_date")) or when(u.findtext(f"{SITEMAP_NS}lastmod"))
        out.append(((u.findtext(f"{SITEMAP_NS}loc") or "").strip(), date))
    return "urls", out


def pick_children(children, now):
    """At most MAX_SITEMAPS child maps: news/post maps first, then the most recent."""
    def key(c):
        loc, date = c
        newsy = any(w in loc.lower() for w in ("news", "post", "article", "press"))
        return (not newsy, -(date.timestamp() if date else 0))
    fresh = [c for c in children if c[0] and (c[1] is None or now - c[1] <= dt.timedelta(days=31))]
    return [loc for loc, _ in sorted(fresh, key=key)[:MAX_SITEMAPS]]


def from_sitemap(fetcher, listing, now):
    """Fresh on-topic addresses of the site map, newest first: [(url, date)], or None when there is no usable map."""
    root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(listing))
    fetcher.allowed(listing)
    rp = fetcher.robots.get(root)
    maps = list((rp.site_maps() if rp else None) or []) or [root + "/sitemap.xml"]
    words, found, queue, read = topic_words(listing), [], maps[:MAX_SITEMAPS], 0
    while queue and read < MAX_SITEMAPS + 1:
        url = queue.pop(0)
        if not same_site(url, listing) or not fetcher.allowed(url):
            continue
        status, _, body = fetcher._get(url)
        read += 1
        if status != 200 or not body:
            continue
        kind, rows = sitemap_urls(body)
        if kind == "index":
            queue = pick_children(rows, now) + queue
            continue
        found += [(u, d) for u, d in rows if u and d and same_site(u, listing) and on_topic(u, words)
                  and dt.timedelta(0) <= now - d <= dt.timedelta(days=FRESH_DAYS)]
    if not found:
        return None
    return sorted(set(found), key=lambda r: r[1], reverse=True)


def extract(html_bytes, url):
    """Text, date and title of an article page (trafilatura 2.3.1, the A5TC-02 pin)."""
    import importlib.metadata
    if importlib.metadata.version("trafilatura") != "2.3.1":
        raise ValueError("EXTRACTOR_VERSION_MISMATCH")
    import trafilatura
    out = trafilatura.extract(html_bytes.decode("utf-8", "replace"), url=url, include_comments=False,
                              include_tables=False, favor_precision=True, output_format="json", with_metadata=True)
    data = json.loads(out) if out else {}
    return {"text": (data.get("text") or "").strip(), "date": data.get("date"), "title": data.get("title")}


class Budget:
    def __init__(self, total=PER_RUN):
        self.left = total


def read_article(fetcher, url, extract_fn):
    if not fetcher.allowed(url):
        return None
    status, headers, body = fetcher._get(url)
    if status != 200 or not body:
        return None
    ctype = (headers.get("Content-Type") or headers.get("content-type") or "text/html").lower()
    if "html" not in ctype:
        return None
    return extract_fn(body, url)


def poll_page(page, fetcher, now, seen, budget, extract_fn=extract, write=False):
    """One page source -> (poll row, shadow rows). `seen`: external ids already remembered for this channel."""
    from collectors.feeds.collect import canonical, capture, entries, external_id
    row = {"channel_id": page["channel_id"], "http_status": None, "etag": None, "last_modified": None,
           "entries": 0, "fresh": 0, "error": None, "method": None}
    listing = page["url"]
    out = []

    def shadow(url, title, published, cap, method, skip, extra=None):
        return {"channel_id": page["channel_id"], "external_id": external_id(url), "url": url,
                "title": (title or "")[:2000], "published_at": published,
                "text_chars": cap.get("text_characters") or 0 if cap else 0,
                "capture_status": (cap or {}).get("status") or skip, "feed_url": listing, "origin_title": None,
                "capture": cap, "transcript": None, "skip_write": bool(skip) or not write,
                "raw_extra": {"collector": COLLECTOR, "page_method": method, **(extra or {})}}

    if not fetcher.allowed(listing):
        row["error"] = "robots"
        return row, out
    status, _, body = fetcher._get(listing)
    row["http_status"] = status
    if status != 200 or not body:
        row["error"] = f"http_{status}"
        return row, out
    links, feeds = parse_page(body, listing)
    words = topic_words(listing)

    # 1. a hidden feed of the same site
    for f in feeds[:1]:
        if not fetcher.allowed(f):
            continue
        fs, _, fbody = fetcher._get(f)
        if fs != 200 or not fbody:
            continue
        got = [e for e in entries(fbody, f, now) if on_topic(e["url"], words)]
        if got:
            row["method"], row["entries"] = "feed", len(got)
            for e in got:
                if external_id(e["url"], e["guid"]) in seen:
                    continue
                out.append(shadow(e["url"], e["title"], e["published_at"], capture(e["html"], now), "feed", None))
            row["fresh"] = len(out)
            return row, out

    # 2. the site map, 3. new headline links of the page
    dated = from_sitemap(fetcher, listing, now)
    if dated:
        row["method"], candidates = "sitemap", [(u, None, d) for u, d in dated]
    else:
        heads = headline_links(links, listing)
        if not heads:
            row["error"] = "no_links"
            return row, out
        row["method"], candidates = "links", [(u, t, None) for u, t in heads]
        if not seen:  # first poll: remember the list, write nothing
            row["entries"] = len(candidates)
            row["baseline"] = True
            return row, [shadow(u, t, now, None, "links", "BASELINE") for u, t, _ in candidates]
    row["entries"] = len(candidates)
    taken = 0
    for url, title, date in candidates:
        url = canonical(url)
        if not url or external_id(url) in seen:
            continue
        if taken >= PER_SOURCE or budget.left <= 0:
            break
        taken += 1
        budget.left -= 1
        try:
            art = read_article(fetcher, url, extract_fn)
        except Exception:  # noqa: BLE001 - one article must not stop the page; the link is remembered
            art = None
        if not art or len(art["text"]) < MIN_TEXT:
            out.append(shadow(url, title, now, None, row["method"], "NOT_ARTICLE"))
            continue
        published = when(art.get("date")) or date or now
        if now - published > dt.timedelta(days=FRESH_DAYS):
            out.append(shadow(url, title or art.get("title"), published, None, row["method"], "OLD"))
            continue
        cap = podcast.as_capture(art["text"], now)
        out.append(shadow(url, art.get("title") or title, published, cap, row["method"], None,
                          {"date_source": "article" if art.get("date") else row["method"] if date else "found"}))
    row["fresh"] = sum(not s["capture_status"] in ("NOT_ARTICLE", "OLD") for s in out)
    return row, out


PAGES_SQL = """
select s.channel_id, s.canonical_url, d.rules_version, d.record ? 'judge', d.reasons, null, null
from source_discovery.source_state s
join lateral (select rules_version, record, reasons from source_discovery.admission_decision a
              where a.channel_id = s.channel_id order by decision_id desc limit 1) d on true
where s.status = 'accepted' and s.delivery = 'page'
order by s.since
"""
SEEN_SQL = ("select channel_id, external_id from source_discovery.feed_items_shadow "
            "where channel_id = any(%s) and seen_at > now() - interval '90 days'")


def run(pg, fetcher, now, write=False, extract_fn=extract, limit=100):
    """All eligible page sources -> (poll rows, shadow rows, counts)."""
    from collectors.feeds.collect import eligible
    cur = pg.cursor()
    cur.execute(PAGES_SQL)
    pages = eligible(cur.fetchall(), limit)
    seen = defaultdict(set)
    if pages:
        cur.execute(SEEN_SQL, ([p["channel_id"] for p in pages],))
        for cid, eid in cur.fetchall():
            seen[cid].add(eid)
    budget, polls, shadow = Budget(), [], []
    for p in pages:
        try:
            row, rows = poll_page(p, fetcher, now, seen[p["channel_id"]], budget, extract_fn, write)
        except Exception as exc:  # noqa: BLE001 - one page must not stop the run; only the error kind is kept
            row, rows = {"channel_id": p["channel_id"], "http_status": None, "etag": None, "last_modified": None,
                         "entries": 0, "fresh": 0, "error": type(exc).__name__, "method": None}, []
        polls.append(row)
        shadow += rows
    counts = {"pages": len(polls), "methods": dict(Counter(r["method"] or "none" for r in polls)),
              "errors": dict(Counter(r["error"] for r in polls if r["error"])),
              "baseline": sum(bool(r.get("baseline")) for r in polls),
              "articles": sum(not s["capture_status"] in ("BASELINE", "NOT_ARTICLE", "OLD") for s in shadow),
              "not_article": sum(s["capture_status"] == "NOT_ARTICLE" for s in shadow),
              "old": sum(s["capture_status"] == "OLD" for s in shadow), "write": write}
    return polls, shadow, counts
