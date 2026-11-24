"""Research without paid Gemini: free web search, then a free model writes the research JSON (KB_RESEARCH=free).

The paid research step used Gemini with Google Search. Its prepaid credits ran out on 23.11.2026 (HTTP 402) and the
owner chose to run without paid search (D-20261116-SYNTHESIS-CEILING, GEMINI_SPEND_20261123). Here a search provider
returns up to five pages per event (Exa first, Tavily when Exa gives nothing), and a free model turns the news, our
archive and those pages into the same RESEARCH_SCHEMA the paid route produced. Nothing is trusted from the model:
the pilot fetches every cited page and build_facts keeps only rows the pages support.

A search failure never stops the run: the event is researched from the news and the archive alone.
"""
import json
import os
import threading
import urllib.error
import urllib.request

from .prompts import RESEARCH_SCHEMA

EXA_URL = "https://api.exa.ai/search"
TAVILY_URL = "https://api.tavily.com/search"
RESULTS = 5
SNIPPET = 1200
DEFAULT_CAP = 20          # searches per run; two runs a day stay inside the free quotas (D2RC-01)

FREE_RESEARCH_SYSTEM = """You are the research desk of a defence-industry analysis service (BD, procurement, supply
chain readers). For one news event you build the background an analyst needs: the event is placed in its programme,
its topic chronology with the status of money, the policy behind it, and the supply chain under it.

You do not search the web yourself. You receive the news, items from our own archive, and numbered web sources found
for this event (url, title, text). Use ONLY these; do not answer from memory. Prefer primary sources (governments,
parliaments, the EU, NATO, procurement and export notices, company press releases) over media.

Rules:
- Every timeline, supply_chain and restrictions row and every document must name where it comes from: source_url is
  exactly the url of one listed web source or of one news item, or archive_id is the id of one archive item.
- Write a row only when its source states it. If the sources say nothing about the history or the supply chain,
  return empty lists. An empty list is correct; an invented row is an error.
- Dates as YYYY-MM-DD (use -01 for an unknown day or month and set precision). Amounts only as the source gives them.
- primary_source is the original announcement of THIS event among the sources, otherwise the first news item."""


def http_post(url, headers, body, timeout=30):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=dict({"Content-Type": "application/json", "User-Agent": "bohun-kb/1"}, **headers))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def exa(key, query, transport=http_post):
    data = transport(EXA_URL, {"x-api-key": key}, {"query": query, "type": "auto", "numResults": RESULTS,
                                                   "contents": {"text": {"maxCharacters": SNIPPET}}})
    return [{"uri": r.get("url"), "title": r.get("title") or "", "text": (r.get("text") or "")[:SNIPPET]}
            for r in (data or {}).get("results") or [] if isinstance(r, dict) and r.get("url")]


def tavily(key, query, transport=http_post):
    data = transport(TAVILY_URL, {"Authorization": "Bearer " + key},
                     {"query": query, "search_depth": "basic", "max_results": RESULTS, "topic": "general"})
    return [{"uri": r.get("url"), "title": r.get("title") or "", "text": (r.get("content") or "")[:SNIPPET]}
            for r in (data or {}).get("results") or [] if isinstance(r, dict) and r.get("url")]


class FreeSearch:
    """search(query) -> [{"uri", "title", "text"}]; at most `cap` calls per run, never raises."""

    def __init__(self, providers, cap=DEFAULT_CAP):
        self.providers, self.cap = providers, cap
        self.used, self.lock = 0, threading.Lock()
        self.stats = {"queries": 0, "skipped_cap": 0, "by_provider": {}, "errors": {}}

    @classmethod
    def from_env(cls, env=os.environ, transport=http_post):
        providers = []
        for name, fn, var in (("exa", exa, "EXA_API_KEY"), ("tavily", tavily, "TAVILY_API_KEY")):
            key = (env.get(var) or "").strip()
            if key:
                providers.append((name, lambda q, fn=fn, key=key: fn(key, q, transport)))
        return cls(providers, int(env.get("KB_FREE_SEARCH_CAP") or DEFAULT_CAP))

    def _note(self, bucket, key):
        self.stats[bucket][key] = self.stats[bucket].get(key, 0) + 1

    def __call__(self, query):
        with self.lock:
            if self.used >= self.cap or not self.providers:
                self.stats["skipped_cap"] += 1
                return []
            self.used += 1
            self.stats["queries"] += 1
        for name, call in self.providers:
            try:
                rows = call(query)
            except urllib.error.HTTPError as exc:
                with self.lock:
                    self._note("errors", "%s_http_%d" % (name, exc.code))
                continue
            except Exception as exc:
                with self.lock:
                    self._note("errors", "%s_%s" % (name, type(exc).__name__))
                continue
            if rows:
                with self.lock:
                    self._note("by_provider", name)
                return rows
        return []


def query_for(ev):
    items, group = ev["items"], ev["group"]
    head = items[0]["title"]
    programme = group.get("programme")
    return ("%s %s" % (head, programme) if programme and programme.lower() not in head.lower() else head)[:300]


def request(cheap, ev, sources):
    """A FreeModel request returning RESEARCH_SCHEMA for one event from news, archive and found web sources."""
    packet = {"news": [{"url": it["url"], "title": it["title"], "outlet": it["outlet"], "date": it["published"][:10],
                        "text": it["text"][:6000]} for it in ev["items"]],
              "programme_hint": ev["group"].get("programme"), "topic": ev["topic"],
              "web_sources": [{"n": k + 1, "url": s["uri"], "title": s["title"], "text": s["text"]}
                              for k, s in enumerate(sources)],
              "our_archive": [{"archive_id": a["archive_id"], "date": a["date"], "title": a["title"],
                               "url": a["url"], "text": a["text"][:600]} for a in ev["archive"]],
              "dossier_known": [{"date": e["row"].get("date"), "what": e["row"].get("what") or e["row"].get("lower")}
                                for e in ev["dossier"]]}
    task = ("Event: %s (%s). Build the research JSON for this event from the material below only."
            % (ev["items"][0]["title"], ev["items"][0]["published"][:10]))
    return cheap.request(FREE_RESEARCH_SYSTEM, packet, RESEARCH_SCHEMA, max_output=8000, task=task, step="research")


def info(sources, searched):
    """The rinfo the pilot expects from the paid route: the found pages stand in for Google's grounding chunks."""
    return {"search_queries": 1 if searched else 0, "supports": [], "cost_micro_usd": 0,
            "grounding_chunks": [{"uri": s["uri"], "title": s["title"]} for s in sources]}
