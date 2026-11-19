"""A1SD-02: ready modules from the A2SA/A1SD comparison (D-20261118-A1SD-READY-MODULES).

- feedparser 6.0.14 (BSD-2): feed parsing -> items with title, link, date.
- lingua-language-detector 2.2.0 (Apache-2.0): language of the titles, restricted to the languages of the geography.
- feedsearch-crawler 2.1.8 (MIT): find feeds on a site; it fetches by itself (aiohttp) with robots.txt respected,
  one request at a time, shallow depth and a total timeout set here.
Runs only in ~/.config/bohun/a1sd02/venv. If a module is missing, callers fall back to our own code.
"""
import datetime as dt

try:
    import feedparser
except ImportError:  # pragma: no cover
    feedparser = None
try:
    from lingua import IsoCode639_1, Language, LanguageDetectorBuilder
except ImportError:  # pragma: no cover
    Language = None

UA = "BOHUN-Source-Discovery/1.0"
GEO_LANGS = ("ENGLISH", "UKRAINIAN", "POLISH", "GERMAN", "FRENCH", "SPANISH", "ITALIAN", "PORTUGUESE", "DUTCH",
             "SWEDISH", "DANISH", "FINNISH", "ESTONIAN", "LATVIAN", "LITHUANIAN", "CZECH", "SLOVAK", "ROMANIAN",
             "BULGARIAN", "HUNGARIAN", "GREEK", "CROATIAN", "SLOVENE", "BOKMAL", "RUSSIAN", "TURKISH")
_detector = None


def parse_feed(body, url, limit=20):
    """feedparser -> [{"url", "title", "date"}]; date from published, or from updated when the entry has no
    published at all (Atom feeds often carry only updated - cycle 1: tagesschau, zaxid were "no dates")."""
    if feedparser is None:
        return None
    parsed = feedparser.parse(body)
    if not parsed.entries and parsed.bozo:
        return []
    items = []
    for e in parsed.entries[:limit]:
        title, link = (e.get("title") or "").strip(), e.get("link") or ""
        stamp, basis = e.get("published_parsed"), "published"
        if not stamp and not e.get("published"):
            stamp, basis = e.get("updated_parsed"), "updated"
        date = dt.datetime(*stamp[:6], tzinfo=dt.timezone.utc).isoformat() if stamp else None
        if title and link:
            items.append({"url": link, "title": title[:240], "date": date, **({"date_basis": basis} if date else {})})
    return items


def detector():
    global _detector
    if _detector is None and Language is not None:
        langs = [getattr(Language, n) for n in GEO_LANGS if hasattr(Language, n)]
        _detector = LanguageDetectorBuilder.from_languages(*langs).with_preloaded_language_models().build()
    return _detector


def detect_language(titles, min_confidence=0.6):
    """Lingua on the joined titles -> (iso 639-1 code or None, confidence)."""
    det = detector()
    if det is None or not titles:
        return None, 0.0
    text = " . ".join(titles)[:4000]
    values = det.compute_language_confidence_values(text)
    if not values:
        return None, 0.0
    best = values[0]
    code = best.language.iso_code_639_1.name.lower()
    return (code if best.value >= min_confidence else None), round(best.value, 3)


def find_feeds(site_url, total_timeout=30, max_depth=2):
    """feedsearch-crawler on one site -> [(feed_url, score)], best first. Network; robots respected by the crawler."""
    from feedsearch_crawler import search
    feeds = search(site_url, concurrency=1, total_timeout=total_timeout, request_timeout=12, max_depth=max_depth,
                   user_agent=UA, respect_robots=True, max_content_length=3 * 1024 * 1024)
    return [(str(f.url), float(getattr(f, "score", 0) or 0)) for f in feeds]
