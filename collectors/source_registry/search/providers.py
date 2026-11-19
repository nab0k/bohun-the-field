"""A1SD-02 search providers behind one shape: search(query, **opts) -> {"results": [{"url", "title"}]}.

Free/no-key: GDELT DOC 2.0 (news, domain + country + language), Wikidata SPARQL (official bodies with websites).
With the owner's keys (D-20261118-A1SD-FREE-PROVIDERS): Ollama web search, Gemini with Google Search grounding.
Tavily stays in cycle.tavily_cli. Keys are read from the owner's files at call time, sent only in headers,
never printed, logged or stored (the reports keep hosts and titles only). Plain urllib, no third-party clients.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KEY_DIR = Path(os.environ["BOHUN_KEY_DIR"]).expanduser() if os.environ.get("BOHUN_KEY_DIR") else None
KEY_ENV = {"Ollama.txt": "OLLAMA_API_KEY", "AI GEMINI GOOGLE.txt": "GEMINI_SEARCH_API_KEY"}
# Wikimedia and GDELT ask automated clients to name a contact; the owner approved the public repository URL (19.11).
CONTACT = os.environ.get("SEARCH_CONTACT_URL", "https://github.com/nab0k/bohun-the-field")
UA = f"BOHUN-SourceSearch/1.0 (+{CONTACT}; low volume, non-commercial)"
_last = {}


def _key(filename):
    """Key from the environment (GitHub secret) or, on the owner's Mac, from his key folder (BOHUN_KEY_DIR)."""
    env = os.environ.get(KEY_ENV.get(filename, ""), "").strip()
    if env:
        return env
    if KEY_DIR is None:
        raise RuntimeError("KEY_MISSING")
    lines = [l.strip() for l in (KEY_DIR / filename).read_text().splitlines() if l.strip()]
    return max(lines, key=len)  # the file may carry a label line next to the key


def tavily_api(job):
    """Tavily over HTTPS with TAVILY_API_KEY (cloud). Same job shape as search_cycle.tavily_cli."""
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        raise RuntimeError("KEY_MISSING")
    body = {"query": job["query"], "topic": job.get("topic", "general"), "max_results": job.get("max_results", 10),
            "search_depth": "basic"}
    if job.get("time_range"):
        body["time_range"] = job["time_range"]
    if job.get("include_domains"):
        body["include_domains"] = job["include_domains"]
    if job.get("exclude_hosts"):
        body["exclude_domains"] = job["exclude_hosts"]
    data = _post("https://api.tavily.com/search", body, {"Authorization": "Bearer " + key})
    return {"results": [{"url": r.get("url"), "title": r.get("title")} for r in data.get("results", [])]}


def _pace(name, seconds):
    wait = _last.get(name, 0) + seconds - time.time()
    if wait > 0:
        time.sleep(wait)
    _last[name] = time.time()


def _get(url, headers=None, timeout=40, retries=2, backoff=20):
    """GET JSON; a 429 (rate limit) waits and retries (comparison 18.11: GDELT and Wikidata answered 429)."""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == retries:
                raise
            time.sleep(backoff * (attempt + 1))


def _post(url, body, headers, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": UA, "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def gdelt(query, country=None, timespan="1month", maxrecords=75):
    """GDELT DOC 2.0 article list. English query (GDELT searches its translated coverage), optional source country
    (GDELT country name, e.g. 'poland'). One request per 5 s as GDELT asks."""
    q = f"({query})" if " OR " in query else query
    if country:
        q += f" sourcecountry:{country}"
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(
        {"query": q, "mode": "artlist", "format": "json", "maxrecords": maxrecords, "timespan": timespan,
         "sort": "datedesc"})
    _pace("gdelt", 10)
    data = _get(url)
    return {"results": [{"url": a.get("url"), "title": a.get("title"), "domain": a.get("domain"),
                         "language": a.get("language"), "sourcecountry": a.get("sourcecountry")}
                        for a in data.get("articles", [])]}


DEFENCE_LABEL = ("defen|militar|armed forces|army|navy|air force|procurement|armament|verteid|bundeswehr|obron|zbroj|"
                 "оборон|збро|försvar|forsvar|puolustus|obran|apăr|aparare|defensa|difesa|défense|defensie|armée|"
                 "ejército|esercito|krijgsmacht|export control")


def _country(country):
    """A country by Wikidata id (Q...) or by ISO code through P297 (A1SD-03: 50 countries without a QID table)."""
    return f"wd:{country}" if country.startswith("Q") else "?c", "" if country.startswith("Q") else f'?c wdt:P297 "{country}". '


def _wikidata(sparql):
    url = "https://query.wikidata.org/sparql?" + urllib.parse.urlencode({"query": sparql, "format": "json"})
    _pace("wikidata", 6)
    data = _get(url, {"Accept": "application/sparql-results+json"}, timeout=70)
    seen, out = set(), []
    for b in data["results"]["bindings"]:
        site = b["site"]["value"]
        if site not in seen:
            seen.add(site)
            out.append({"url": site, "title": (b.get("label") or {}).get("value", "")})
    return {"results": out}


# Checked on Wikidata 19.11 (read-only): Q392933 = arms industry, Q3477363 = aerospace industry; a company query by
# industry answered in 7-32 s (PL 27, DE 76 sites). Trade associations by label timed out (504) twice: associations
# stay with the search layer "types".
DEFENCE_INDUSTRIES = ("Q392933", "Q3477363")


def wikidata_companies(country):
    """Companies of a country in the arms or aerospace industry (P452) with their official websites."""
    c, bind = _country(country)
    inds = " ".join("wd:" + q for q in DEFENCE_INDUSTRIES)
    return _wikidata(f"""
SELECT DISTINCT ?item ?label ?site WHERE {{
  {bind}?item wdt:P17 {c}; wdt:P856 ?site; wdt:P452 ?ind.
  VALUES ?ind {{ {inds} }}
  OPTIONAL {{ ?item rdfs:label ?label FILTER(lang(?label) = "en") }}
}} LIMIT 200""")


def wikidata_officials(country):
    """Government bodies of a country whose label says defence/military/procurement, with their official websites."""
    c, bind = _country(country)
    sparql = f"""
SELECT DISTINCT ?item ?label ?site WHERE {{
  {bind}?item wdt:P17 {c}; wdt:P856 ?site;
        wdt:P31/wdt:P279* ?kind .
  VALUES ?kind {{ wd:Q327333 wd:Q192350 wd:Q2659904 }}
  ?item rdfs:label ?label .
  FILTER(REGEX(LCASE(?label), "{DEFENCE_LABEL}"))
}} LIMIT 60"""
    return _wikidata(sparql)


def ollama(query, max_results=10):
    _pace("ollama", 1)
    data = _post("https://ollama.com/api/web_search", {"query": query, "max_results": max_results},
                 {"Authorization": "Bearer " + _key("Ollama.txt")})
    return {"results": [{"url": r.get("url"), "title": r.get("title")} for r in data.get("results", [])]}


def gemini_grounded(query, model="gemini-3.5-flash"):  # 2.5 is closed to new users (404, 18.11)
    """Gemini with the google_search tool: the grounding chunks name the publishers (title = domain); the uri is a
    Google redirect, so the domain from the title is used."""
    _pace("gemini", 4)
    body = {"contents": [{"parts": [{"text": f"Find recent news websites and official sources publishing about: {query}. "
                                             "List the publishers."}]}],
            "tools": [{"google_search": {}}]}
    data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", body,
                 {"x-goog-api-key": _key("AI GEMINI GOOGLE.txt")})
    out = []
    for cand in data.get("candidates", []):
        for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks", []):
            web = ch.get("web") or {}
            title = (web.get("title") or "").strip()
            if title and "." in title and " " not in title:
                out.append({"url": f"https://{title}/", "title": title})
    return {"results": out}
