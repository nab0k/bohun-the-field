"""Level-1 fallback for sites without RSS: ordinary sitemap.xml with <lastmod>.

A sitemap has no titles, so an item's title is derived from the URL slug
(".../news/2026/10/rheinmetall-signs-ammunition-contract" -> "rheinmetall signs ammunition contract").
<lastmod> is a modification date, not a publication date; items carry date_source=sitemap_lastmod.
Article pages are never fetched.
"""

import datetime as dt
import re
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

NEWS_PATH_HINTS = (
    "news", "press", "media", "release", "article", "post", "blog", "update", "insight", "story",
    "aktuell", "nachricht", "meldung", "presse", "actualit", "communique", "noticia", "notizi", "comunicat",
    "nieuws", "actueel", "nyhet", "nyheder", "aktuellt", "uutis", "ajankoht", "uudis", "jaunum", "naujien",
    "zprav", "novink", "aktualn", "wiadomo", "stiri", "știri", "eidhse", "noviny", "hirek", "novic",
)
MAX_SITEMAP_BYTES = 10_000_000
SLUG_STOP = re.compile(r"\.(html?|php|aspx?)$", re.I)


def _parse_date(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            value = dt.datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def is_news_path(url):
    path = unquote(urlsplit(url).path).lower()
    return any(hint in path for hint in NEWS_PATH_HINTS)


def title_from_url(url):
    segments = [s for s in unquote(urlsplit(url).path).split("/") if s]
    for segment in reversed(segments):
        segment = SLUG_STOP.sub("", segment)
        words = re.split(r"[-_+]+", segment)
        words = [w for w in words if w and not re.fullmatch(r"\d+|[0-9a-f]{8,}", w, re.I)]
        if len(words) >= 3:
            return " ".join(words)
    return ""


def parse(content):
    """Return ('index', [(loc, lastmod)]) or ('urlset', [(loc, lastmod)])."""
    soup = BeautifulSoup(content[:MAX_SITEMAP_BYTES], "xml")
    if soup.find("sitemapindex"):
        kind, tag = "index", "sitemap"
    elif soup.find("urlset"):
        kind, tag = "urlset", "url"
    else:
        return None, []
    rows = []
    for node in soup.find_all(tag):
        loc = node.find("loc")
        if loc and loc.text.strip():
            last = node.find("lastmod")
            rows.append((loc.text.strip(), _parse_date(last.text if last else "")))
    return kind, rows


def recent_news_urls(rows, *, days_back=60, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=days_back)
    dated = [(loc, date) for loc, date in rows if date and cutoff <= date <= now + dt.timedelta(days=1)]
    news = [(loc, date) for loc, date in dated if is_news_path(loc)]
    return news or dated


def _pick_children(rows, limit=3):
    hinted = [row for row in rows if is_news_path(row[0])]
    pool = hinted or rows
    pool = sorted(pool, key=lambda r: r[1] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return [loc for loc, _ in pool[:limit]]


def find_sitemap(site, client, *, days_back=60, min_items=3, now=None):
    """Return (sitemap_url, recent_rows) for the child sitemap with most recent news-like URLs, or (None, [])."""
    parsed = urlsplit(site)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    roots = []
    try:
        robots = client.get(origin + "/robots.txt", check_robots=False)
        if robots.status_code == 200:
            roots += [line.split(":", 1)[1].strip() for line in robots.text.splitlines()
                      if line.lower().startswith("sitemap:")]
    except Exception:
        pass
    roots += [urljoin(origin + "/", p) for p in ("sitemap.xml", "sitemap_index.xml", "wp-sitemap.xml")]
    best, best_rows, seen = None, [], set()
    for root in roots[:5]:
        if root in seen or root.endswith(".gz"):
            continue
        seen.add(root)
        try:
            response = client.get(root)
            if response.status_code != 200:
                continue
            kind, rows = parse(response.content)
        except Exception:
            continue
        targets = [(root, rows)] if kind == "urlset" else []
        if kind == "index":
            for child in _pick_children(rows):
                if child in seen or child.endswith(".gz"):
                    continue
                seen.add(child)
                try:
                    child_response = client.get(child)
                    if child_response.status_code == 200:
                        child_kind, child_rows = parse(child_response.content)
                        if child_kind == "urlset":
                            targets.append((child, child_rows))
                except Exception:
                    continue
        for url, rows in targets:
            recent = recent_news_urls(rows, days_back=days_back, now=now)
            if len(recent) > len(best_rows):
                best, best_rows = url, recent
        if len(best_rows) >= min_items:
            break
    return (best, best_rows) if len(best_rows) >= min_items else (None, [])
