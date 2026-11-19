"""Discover Polish association homepages from public structured directories."""
import json
import re
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit

from .policy import Problem, identity_url

KIG = "https://kig.pl/izby-gospodarcze/"
WIKIDATA = "https://query.wikidata.org/sparql"
SCOPE = ("PL", "association", "pl")
ASSOCIATION = re.compile(r"\b(izba|stowarzyszenie|związek|federacja|konfederacja)\b", re.I)
STOP = {"CYCLE_TIME_CAP", "HTTP_REQUEST_CAP", "HTTP_BYTE_CAP", "DNS_TIMEOUT", "DNS_WORKER_HOLD"}


class KIGMembers(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.name = ""
        self.heading = None
        self.cells = None
        self.text = None
        self.links = None

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == "p" and "question" in (data.get("class") or "").split():
            self.heading = []
        if tag == "tr":
            self.cells = []
        if tag == "td" and self.cells is not None:
            self.text, self.links = [], []
        if tag == "a" and self.links is not None and data.get("href"):
            self.links.append(data["href"])

    def handle_data(self, text):
        if self.heading is not None:
            self.heading.append(text)
        if self.text is not None:
            self.text.append(text)

    def handle_endtag(self, tag):
        if tag == "p" and self.heading is not None:
            self.name = " ".join(" ".join(self.heading).split())[:200]
            self.heading = None
        if tag == "td" and self.text is not None and self.cells is not None:
            self.cells.append((" ".join(" ".join(self.text).split()), self.links))
            self.text, self.links = None, None
        if tag == "tr" and self.cells is not None:
            if len(self.cells) >= 2 and self.cells[0][0].casefold() == "www" and ASSOCIATION.search(self.name):
                text, links = self.cells[1]
                # KIG sometimes exposes a valid website as text in a mailto link.
                web_links = [url for url in links if url.startswith(("https://", "http://"))]
                self.rows.extend((self.name, url) for url in (web_links or [text]))
            self.cells, self.text, self.links = None, None, None


def kig_rows(http):
    response = http.request(KIG, robots=True, limit=1_000_000)
    if response.status != 200:
        raise Problem(f"HTTP_{response.status}")
    parser = KIGMembers()
    parser.feed(response.body.decode("utf-8", errors="replace"))
    parser.close()
    return [(label, url, response.url) for label, url in parser.rows]


def wikidata_rows(http, limit):
    # Explicit classes keep topical and country scope independent of text labels.
    query = (
        "SELECT DISTINCT ?item ?name ?site WHERE { "
        "?item wdt:P17 wd:Q36 ; wdt:P31 wd:Q2178147 ; wdt:P856 ?site . "
        "OPTIONAL { ?item rdfs:label ?name FILTER(LANG(?name) = 'pl') } "
        f"}} ORDER BY ?item ?site LIMIT {limit}"
    )
    url = WIKIDATA + "?" + urlencode({"format": "json", "query": query})
    response = http.request(
        url, robots=True, limit=150_000,
        headers={"Accept": "application/sparql-results+json",
                 "User-Agent": "BOHUN-A1SD/0.1 (+https://bohundefence.com/)"},
    )
    if response.status != 200:
        raise Problem(f"HTTP_{response.status}")
    try:
        rows = json.loads(response.body)["results"]["bindings"]
        return [(row.get("name", row["item"])["value"], row["site"]["value"], row["item"]["value"])
                for row in rows]
    except (ValueError, KeyError, TypeError):
        raise Problem("WIKIDATA_RESPONSE_INVALID") from None


def website(raw, normalize):
    candidate = raw.strip()
    if candidate.startswith("http://"):
        candidate = "https://" + candidate[len("http://"):]
    elif "://" not in candidate and not candidate.startswith(("/", "mailto:", "#")):
        candidate = "https://" + candidate
    try:
        return normalize(candidate)
    except Problem:
        return None


def populate(scope, http, normalize, visited, *, limit, providers=("kig", "wikidata")):
    if tuple(scope) != SCOPE:
        raise ValueError("supported population scope is PL association pl")
    result, audit = {}, []
    for provider in providers:
        try:
            rows = kig_rows(http) if provider == "kig" else wikidata_rows(http, limit)
        except Problem as exc:
            audit.append({"provider": provider, "reason": str(exc), "sites": 0})
            if str(exc) in STOP:
                break
            continue
        accepted = 0
        for label, raw_url, provenance in rows:
            url = website(raw_url, normalize)
            if not url or url in result:
                continue
            # Reuse the same publisher URL for subsequent cycles.
            try:
                provenance = identity_url(provenance.replace("http://www.wikidata.org/", "https://www.wikidata.org/", 1))
            except Problem:
                continue
            result[url] = {
                "url": url, "label": label[:200], "country": scope[0], "type": scope[1],
                "language": scope[2], "mode": "publisher",
                "provider": provider, "provider_url": provenance,
            }
            accepted += 1
        audit.append({"provider": provider, "reason": "ok" if accepted else "empty", "sites": accepted})
        if sum(url not in visited for url in result) >= limit:
            break
    seeds = sorted(result.values(), key=lambda seed: (visited.get(seed["url"], 0), seed["url"]))[:limit]
    return seeds, audit
