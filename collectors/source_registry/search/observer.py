"""A1SD-02 live observer: what the network shows about one candidate, as input for admission.decide().

Reuses Codex's bounded discovery transport (EXP-033 autonomous-search/candidate): robots.txt before every page,
2 s per host, request/byte/time caps, at most 3 same-origin redirects, public addresses only. No login, no
scripts, no workarounds. Network only with the explicit `enabled=True` from the runner (separate permission).
Nothing of the pages is stored here except titles, dates and links of up to 20 items (no article text).
"""
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from codex_discovery import cycle  # noqa: E402
from codex_discovery.policy import Problem  # noqa: E402
from codex_discovery.publication_evidence import PublicationPage, folded  # noqa: E402
from codex_discovery.radars import feed_data  # noqa: E402

# News/press section words in the languages of the geography (folded, no diacritics). Codex's list was PL+EN.
SECTION = re.compile(
    r"\b(news|newsroom|pressroom|press|press-releases?|media|stories|blog|updates|insights|articles|announcements|"
    r"aktualnosci|wiadomosci|komunikaty|nachrichten|aktuelles|presse|neuigkeiten|"
    r"pressemitteilungen|actualites|communiques|noticias|actualidad|prensa|notizie|comunicati|novidades|"
    r"nieuws|pers|nyheter|nyheder|uutiset|tiedotteet|ajankohtaista|aktuality|novinky|tiskove-zpravy|uudised|"
    r"jaunumi|naujienos|stiri|novini|hirek|vijesti|novice|novyny|novosti|press-centre|press-center|"
    r"новини|новости|актуално|пресцентър|пресс-центр|прес-центр|медиа|новини-и-събития)\b")
FEED_GUESSES = ("/feed/", "/rss.xml", "/rss")  # common feed addresses when the page links none (18.11 fix)
# Tried as news pages only when the home page links no section at all (cycle 1: mod.gov.ua had none in its HTML).
SECTION_GUESSES = ("/news", "/en/news")
MONTHS = {m: i + 1 for i, names in enumerate((
    "january januar janvier enero gennaio stycznia sichnia січня ledna tammikuuta januari ianuarie",
    "february februar fevrier febrero febbraio lutego liutoho лютого unora helmikuuta februari februarie",
    "march marz mars marzo marca bereznia березня brezna maaliskuuta maart martie",
    "april avril abril aprile kwietnia kvitnia квітня dubna huhtikuuta aprilie",
    "may mai mayo maggio maja travnia травня kvetna toukokuuta mei maj",
    "june juni juin junio giugno czerwca chervnia червня cervna kesakuuta iunie",
    "july juli juillet julio luglio lipca lypnia липня cervence heinakuuta iulie",
    "august aout agosto sierpnia serpnia серпня srpna elokuuta augustus augusti",
    "september septembre septiembre settembre wrzesnia veresnia вересня zari syyskuuta septembrie",
    "october oktober octobre octubre ottobre pazdziernika zhovtnia жовтня rijna lokakuuta octombrie",
    "november novembre noviembre listopada lystopada листопада listopadu marraskuuta noiembrie",
    "december dezember decembre diciembre dicembre grudnia hrudnia грудня prosince joulukuuta decembrie"))
    for m in names.split()}
_D_NUM = re.compile(r"\b(20\d\d)[-/.](\d{1,2})[-/.](\d{1,2})\b|\b(\d{1,2})\.(\d{1,2})\.(20\d\d)\b")  # day.month.year only: 05/11/2026 is ambiguous
_D_TEXT = re.compile(r"\b(\d{1,2})\.?\s+([^\W\d_]{3,12})\.?,?\s+(20\d\d)\b|\b([^\W\d_]{3,12})\.?\s+(\d{1,2}),?\s+(20\d\d)\b")
_D_PATH = re.compile(r"/(20\d\d)/(\d{1,2})/(\d{1,2})(?:/|$)|/(20\d\d)-(\d{2})-(\d{2})")
HOME_LIMIT = 3_000_000  # large home pages were wrongly "unreachable" at 1 MB (18.11 fix)
STOP = {"CYCLE_TIME_CAP", "HTTP_REQUEST_CAP", "HTTP_BYTE_CAP", "DNS_TIMEOUT", "DNS_WORKER_HOLD"}
TRANSIENT = {"TRANSPORT_FAILURE", "REQUEST_TIMEOUT", "DNS_TIMEOUT", "DNS_UNAVAILABLE"}  # 18.11: a local DNS outage is not a dead site


def _mime(response):
    return response.headers.get("content-type", "").split(";", 1)[0].lower()


def _is_feed(response):
    m = _mime(response)
    return m.endswith("xml") or m in ("application/rss+xml", "application/atom+xml") or \
        response.body.lstrip()[:200].lower().startswith((b"<?xml", b"<rss", b"<feed"))


def section_links(home, body):
    page = cycle.html_links(body)
    host = urlsplit(home).hostname.removeprefix("www.")
    out, later = [], []
    for href in page.alternates:
        url = cycle.safe_link(home, href)
        if url and urlsplit(url).hostname.removeprefix("www.") == host:
            out.append((url, "feed"))
    for href, label in page.links:
        url = cycle.safe_link(home, href)
        if not url or url == home or urlsplit(url).hostname.removeprefix("www.") != host:
            continue
        if SECTION.search(folded(label).lower()):
            out.append((url, "page"))
        elif SECTION.search(folded(urlsplit(url).path.replace("/", " ").replace("-", " ")).lower()):
            later.append((url, "page"))
    return list(dict.fromkeys(out + later))


try:  # D-20261118-A1SD-READY-MODULES
    import ready_modules as _ready
    if _ready.feedparser is None:
        _ready = None
except Exception:  # noqa: BLE001
    _ready = None


def feed_items(response, url):
    """feedparser when available (item links on the same site only), our parser otherwise."""
    if _ready is not None:
        site = urlsplit(url).hostname.removeprefix("www.")
        items = [i for i in (_ready.parse_feed(response.body, url) or [])
                 if (urlsplit(i["url"]).hostname or "").removeprefix("www.") == site]
        if items:
            return items
    try:
        return cycle.dated_feed_items(response, url) or [
            {"url": e["url"], "title": e["title"][:240], "date": None} for e in feed_data(response.body, url)["entries"][:20]]
    except Problem:
        return []


def _ymd(y, m, d):
    import datetime as dt
    try:
        return dt.datetime(int(y), int(m), int(d), tzinfo=dt.timezone.utc).isoformat()
    except ValueError:
        return None


def date_in(text, path=""):
    """A publication date written in a link's text ("17 November 2026 ...", "17.11.2026") or address
    (/2026/11/17/). Used only for section links that carry no machine-readable date (cycle 1: Saab, Bundeswehr)."""
    t = folded(text or "").lower()
    m = _D_NUM.search(t)
    if m:
        return _ymd(m[1], m[2], m[3]) if m[1] else _ymd(m[6], m[5], m[4])
    m = _D_TEXT.search(t)
    if m:
        day, name, year = (m[1], m[2], m[3]) if m[1] else (m[5], m[4], m[6])
        month = MONTHS.get(name) or next((v for k, v in MONTHS.items() if len(name) >= 3 and k.startswith(name)), None)
        if month:
            return _ymd(year, month, day)
    m = _D_PATH.search(path or "")
    if m:
        return _ymd(m[1], m[2], m[3]) if m[1] else _ymd(m[4], m[5], m[6])
    return None


def undated_section_items(body, url, minimum=5):
    """Article-like links below a news section path; used only when the section has no dates (check 7 -> disputed)."""
    base = urlsplit(url)
    prefix = base.path.rstrip("/") + "/"
    out = {}
    for href, label in cycle.html_links(body).links:
        link = cycle.safe_link(url, href)
        if not link or urlsplit(link).hostname != base.hostname:
            continue
        path = urlsplit(link).path
        deep = path.startswith(prefix) and len(path) > len(prefix) + 3
        article_like = path.count("/") >= 2 and len(path) > 15 and not SECTION.fullmatch(path.strip("/").split("/")[-1])
        if (deep or article_like) and len(label.split()) >= 4:
            date = date_in(label, path)
            out.setdefault(link, {"url": link, "title": label[:240], "date": date,
                                  **({"date_basis": "link_text_or_path"} if date else {})})
    return list(out.values())[:20] if len(out) >= minimum else []


OFFICIAL_HOST = re.compile(r"(^|\.)(gov|mil|gouv|gob|bund|mod)\.|\.gov(\.[a-z]{2})?$|\.mil$")
GENERAL_SHARE = 0.10   # half of the admission threshold (0.20): borderline sites still get the full check
GENERAL_MIN_LINKS = 10


def home_general(candidate, url, body):
    """Cheap first stage: on the home page, what share of headline-like links (4+ words) is about defence.
    Official hosts and sites whose own name says defence always get the full check."""
    import admission as A
    host = urlsplit(url).hostname or ""
    if OFFICIAL_HOST.search(host) or (candidate.get("found_via") or {}).get("focus"):
        return None
    labels = [label for _, label in cycle.html_links(body).links if len(label.split()) >= 4]
    share, n = A.topic_share(labels[:200])
    if n >= GENERAL_MIN_LINKS and share < GENERAL_SHARE:
        return {"home_share": round(share, 3), "home_links": n}
    return None


def observe(candidate, http, max_targets=3, use_feedsearch=False):
    """Returns an observation dict for admission.decide(); network errors are mapped to its checks."""
    url = candidate["url"]
    obs = {"candidate": candidate, "fetch": {}, "robots": {}, "delivery": None, "delivery_url": None, "items": []}
    try:
        home = http.request(url, robots=True, limit=HOME_LIMIT)
    except Problem as exc:
        code = str(exc)
        if code in STOP:
            raise
        if code == "URL_HTTPS_REQUIRED":  # the site sends us to plain http; alive but not readable over TLS
            obs["fetch"] = {"status": "http_only"}
        elif code == "ROBOTS_DISALLOWED":
            obs["fetch"] = {"status": 200, "final_url": url}
            obs["robots"] = {"state": "rules", "allowed": False}
        elif code in ("ROBOTS_UNAVAILABLE", "ROBOTS_UNCERTAIN"):
            obs["fetch"] = {"status": 200, "final_url": url}
            obs["robots"] = {"state": "unreadable", "code": code}
        else:
            obs["fetch"] = {"status": "connect_error" if code in TRANSIENT else code}
        return obs
    obs["fetch"] = {"status": home.status, "final_url": home.url}
    obs["robots"] = {"state": "rules", "allowed": True}
    if home.status != 200:
        return obs
    final = cycle.channel_url(home.url)
    if _is_feed(home):
        obs.update(delivery="feed", delivery_url=final, items=feed_items(home, final))
        return obs
    if _mime(home) != "text/html":
        return obs
    page = PublicationPage(home.body)
    obs["html_lang"] = page.lang or page.metadata.get("og:locale", "").replace("_", "-")
    obs["site_name"] = (page.metadata.get("og:site_name") or page.title or "").strip()[:200]
    general = home_general(candidate, final, home.body) if candidate.get("prefilter") else None
    if general:  # D-20261118-A1SD-RETRO-PLAN-G2: a general outlet costs one page, not a feed hunt
        obs["general_outlet"] = general
        return obs
    own = cycle.dated_page_items(page, final, http) if candidate.get("mode") == "channel" else []
    if len(own) >= 2:
        obs.update(delivery="page", delivery_url=final, items=own)
        return obs
    undated = None
    root = "https://" + urlsplit(final).netloc
    targets = sorted(section_links(final, home.body), key=lambda t: t[1] != "feed")[:max_targets]
    if not any(route == "page" for _, route in targets):
        targets += [(root + g, "section_guess") for g in SECTION_GUESSES]
    targets += [(root + g, "guess") for g in FEED_GUESSES]
    for target, route in targets:
        try:
            r = http.request(target, robots=True, limit=HOME_LIMIT)
        except Problem as exc:
            if str(exc) in STOP:
                raise
            continue
        if r.status != 200:
            continue
        turl = cycle.channel_url(r.url)
        if urlsplit(turl).hostname.removeprefix("www.") != urlsplit(final).hostname.removeprefix("www."):
            continue
        if _is_feed(r):
            items = feed_items(r, turl)
            if len(items) >= 2:
                obs.update(delivery="feed", delivery_url=turl, items=items)
                return obs
        elif _mime(r) == "text/html" and route != "guess":
            items = cycle.dated_page_items(PublicationPage(r.body), turl, http)
            if len(items) >= 2:
                obs.update(delivery="page", delivery_url=turl, items=items)
                return obs
            undated = undated or (turl, undated_section_items(r.body, turl))
    if _ready is not None and use_feedsearch:  # feedsearch-crawler: own fetches, robots respected, 1 at a time
        try:
            found = _ready.find_feeds(final)
        except Exception:  # noqa: BLE001 - the crawler failing must not stop the cycle
            found = []
        obs["feedsearch"] = len(found)
        for feed_url, _score in found[:2]:
            if urlsplit(feed_url).hostname.removeprefix("www.") != urlsplit(final).hostname.removeprefix("www."):
                continue
            try:
                r = http.request(feed_url, robots=True, limit=HOME_LIMIT)
            except Problem as exc:
                if str(exc) in STOP:
                    raise
                continue
            if r.status == 200 and _is_feed(r):
                items = feed_items(r, cycle.channel_url(r.url))
                if len(items) >= 2:
                    obs.update(delivery="feed", delivery_url=cycle.channel_url(r.url), items=items, feed_found_by="feedsearch")
                    return obs
    if undated and undated[1]:
        obs.update(delivery="page", delivery_url=undated[0], items=undated[1])
    return obs
