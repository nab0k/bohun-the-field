"""A1SD-02 search cycle by the approved design: plan -> Tavily -> candidates -> observe -> admit -> measure.

  # plan only, no network (default):
  /opt/homebrew/bin/python3.13 -I search/cycle.py --cycle 1 --store ~/.local/share/bohun/a1sd-02/search
  # live cycle - only after the owner's permission for that run:
  ~/.config/bohun/a1sd02/venv/bin/python -I search/cycle.py --cycle 1 --store ... --events events.json --network

Limits per cycle (design, D-20261118-A1SD-SEARCH-DESIGN-G2): Tavily <= 100 queries, HTTP <= 700 requests, new
candidates <= 150. The shadow registry and the cycle state live in --store (outside the repository); nothing is
written to the product database. Reports keep counts, hosts and decisions, never article texts.
"""
import argparse
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plan as P  # noqa: E402
import admission as A  # noqa: E402

NOT_PUBLISHERS = {"youtube.com", "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
                  "wikipedia.org", "reddit.com", "news.google.com", "msn.com", "yahoo.com", "tiktok.com",
                  "medium.com", "substack.com", "t.me", "telegram.org", "apple.com", "spotify.com"}

AGGREGATORS = NOT_PUBLISHERS | {"news.yahoo.com", "flipboard.com", "newsbreak.com", "ground.news", "inoreader.com",
                                "feedly.com", "bing.com", "google.com", "duckduckgo.com", "archive.org", "scribd.com"}
LIMITS = {"tavily": 60, "gemini": 60, "http": 900, "new": 120}   # cycle 4 (D-20261119-A1SD-SEARCH-CYCLE-4)
QUOTAS = {"types": 48, "layer1": 36, "layer3": 24, "section": 12}   # = 120 queries (cycle 5: + types layer)
NEW_CAPS = {"types": 48, "layer1": 36, "layer3": 24, "section": 12}  # = 120 new candidates, in proportion
# Gemini queries per layer; the rest of each layer goes to Tavily. Sections need a domain filter -> Tavily only.
GEMINI_SHARE = {"types": 36, "layer1": 12, "layer3": 12, "section": 0}   # = 60; Tavily = 12 + 24 + 12 + 12 = 60


def tavily_cli(job):
    cmd = ["tvly", "search", job["query"], "--topic", job["topic"], "--max-results", str(job["max_results"]),
           "--depth", "basic", "--json"]
    if job.get("time_range"):
        cmd += ["--time-range", job["time_range"]]
    if job.get("exclude_hosts"):
        cmd += ["--exclude-domains", ",".join(job["exclude_hosts"])]
    if job.get("include_domains"):
        cmd += ["--include-domains", ",".join(job["include_domains"])]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError("TAVILY_CLI_FAILED")
    return json.loads(out.stdout)


def general_sites(conn):
    """Sites held as general outlets in the shadow registry, with their language, oldest first."""
    rows = conn.execute("SELECT canonical_url, language FROM source_state WHERE status='general_outlet' "
                        "ORDER BY since").fetchall()
    out, seen = [], set()
    for url, lang in rows:
        site = A.site_of(A.registry.host_of(url))
        if site and site not in seen:
            seen.add(site)
            out.append({"site": site, "language": lang})
    return out


def assign_engines(todo, gemini_share):
    """Inside each layer alternate Gemini and Tavily until the layer's Gemini share is used: both engines see the
    same kinds of questions (comparison 19.11: they barely overlap, so each finds what the other misses)."""
    total = Counter(j["layer"] for j in todo)
    seen, given = Counter(), Counter()
    for job in todo:
        layer = job["layer"]
        want, left = gemini_share.get(layer, 0) - given[layer], total[layer] - seen[layer]
        if job.get("engine") in ("gdelt", "wikidata"):  # open bases: the layer fixes the engine
            seen[layer] += 1
            continue
        job["engine"] = "gemini" if want > 0 and (seen[layer] % 2 == 0 or want >= left) else "tavily"
        given[layer] += job["engine"] == "gemini"
        seen[layer] += 1
    return todo


class Engines:
    """search(job) by job["engine"]. A provider that fails twice in a row is switched off for the rest of the
    cycle (owner 19.11: stop at once if the free Gemini quota ends or starts billing)."""

    def __init__(self, tavily=None, gemini=None):
        import providers as PR
        self.fn = {"tavily": tavily or tavily_cli, "gemini": gemini or (lambda job: PR.gemini_grounded(job["query"])),
                   "gdelt": lambda job: PR.gdelt(job["query"], job.get("gdelt_country")),
                   "wikidata": lambda job: PR.wikidata_officials(job["qid"])}
        self.streak, self.off, self.used, self.errors = Counter(), set(), Counter(), Counter()

    def __call__(self, job):
        engine = job.get("engine", "tavily")
        if engine in self.off:
            raise RuntimeError(f"{engine.upper()}_OFF")
        self.used[engine] += 1
        try:
            data = self.fn[engine](job)
        except Exception as exc:  # noqa: BLE001
            # cloud run 1 (19.11): Gemini switched off with no visible reason; keep the error kind, never the message
            self.errors[f"{engine}:{type(exc).__name__}:{getattr(exc, 'code', '')}"] += 1
            self.streak[engine] += 1
            if self.streak[engine] >= 2:
                self.off.add(engine)
            raise RuntimeError(f"{engine.upper()}_FAILED") from exc
        self.streak[engine] = 0
        return data


def known_hosts(store_conn, base_hosts):
    hosts = {A.site_of(h) for h in base_hosts}
    for (h,) in store_conn.execute("SELECT host FROM channel"):
        if h:
            hosts.add(A.site_of(h))
    for (u,) in store_conn.execute("SELECT candidate_url FROM candidate_seen"):
        h = A.registry.host_of(u)
        if h:
            hosts.add(A.site_of(h))
            if urlsplit(u).path.strip("/"):
                hosts.add(A.registry.canonical_url(u))  # a section already tried is known by its address
    return hosts


FOCUS = re.compile(r"defen[cs]|milit|army|armed|navy|naval|aero|space|drone|uav|obron|zbroj|wojsk|armee|rustung|"
                   r"ruestung|verteidig|difesa|defensa|puolustus|forsvar|oboron|armad|armament|weapon|ordnance|"
                   r"оборон|війсь|арм")


def focus(site):
    """1 when the site's own name says defence/military/aerospace (defence24, zona-militar, armadninoviny)."""
    return 1 if FOCUS.search(site) else 0


SECTION_PATH = re.compile(r"defen[cs]|verteidig|difesa|defensa|defensie|forsvar|puolustus|obrana|obron|oborona|"
                          r"оборон|aparare|military|militar|armee|armed-forces|security-and-defen")


def section_candidate(seen, job, result):
    """A result inside the site whose address has a defence segment -> that section (cut after the segment) is the
    candidate. Articles without such a segment say nothing about a section and are skipped."""
    parts = urlsplit(result.get("url") or "")
    if re.search(r"\.(pdf|docx?|xlsx?|pptx?)$", parts.path.lower()):
        return  # cycle 3: documents were taken for sections
    segs = [p for p in parts.path.split("/") if p]
    for i, seg in enumerate(segs):
        word = unquote(seg).lower()
        # a section is a short segment (defense, verteidigung, forsvarsdepartementet) with more below it;
        # cycle 3 cut article slugs ("kw37-de-verteidigung-1194766") and took the article for a section
        if SECTION_PATH.search(word) and i < len(segs) - 1 and len(re.split(r"[-_]", word)) <= 3:
            url = f"https://{parts.netloc}/" + "/".join(segs[:i + 1]) + "/"
            key = "section:" + url
            info = seen.setdefault(key, {"order": len(seen), "queries": set(), "by_layer": {}})
            info["queries"].add(P.job_key(job))
            info["by_layer"].setdefault("section", {
                "url": url, "pool_group": "section", "mode": "channel", "section_of": job["site"],
                "found_via": {"channel": "tavily", "layer": "section", "query": job["query"], "country": None,
                              "type_hint": job["type_hint"], "lang": job["lang"], "site": job["site"],
                              "result_url": result["url"]}})
            return


def collect(jobs, search, known, max_queries, max_new, layer_caps=None, raw=None):
    """Run the jobs (each = 1 query) -> publisher candidates. A failed query is skipped, the rest go on (spec §7).
    Every found site is counted (for saturation). New sites are ranked before the caps are applied: a site found by
    more queries and a site whose own name says defence come first (cycle 1: general news sites from news search
    filled the 150 and then failed the topic check). Caps: max_new in all and layer_caps[layer] per layer (cycle 1:
    the first layers filled the 150 and layer 3 got none). The query's country and type are kept in found_via only:
    they are hints, not evidence (cycle 1: a think tank found by "procurement agency" skipped the topic check)."""
    found, seen, done, used, failed = [], {}, [], 0, 0
    for order, job in enumerate(jobs):
        if used >= max_queries:
            break
        used += 1
        try:
            data = search(job)
        except (RuntimeError, ValueError, subprocess.TimeoutExpired):
            failed += 1
            continue
        done.append(P.job_key(job))
        exclude = {A.site_of(h) for h in job.get("exclude_hosts", [])}
        for r in data.get("results", []):
            host = A.registry.host_of(r.get("url") or "")
            if raw is not None:  # what the search showed, for offline replays (titles and links, no text)
                raw.append({"job": P.job_key(job), "layer": job["layer"], "engine": job.get("engine", "tavily"),
                            "url": r.get("url"),
                            "title": (r.get("title") or "")[:240], "score": r.get("score")})
            if not host:
                continue
            site = A.site_of(host)
            if site in AGGREGATORS or site in exclude:
                continue
            if job["layer"] == "section":  # a defence section of a site we already hold as general
                section_candidate(seen, job, r)
                continue
            is_known = site in known
            found.append({"site": site, "layer": job["layer"], "known": is_known})
            if is_known:
                continue
            info = seen.setdefault(site, {"order": len(seen), "queries": set(), "by_layer": {}})
            info["queries"].add(P.job_key(job))
            info["by_layer"].setdefault(job["layer"], {   # first finding per layer; the site may fill any of them
                "url": f"https://{urlsplit(r['url']).netloc}/", "pool_group": job["layer"],
                "found_via": {"channel": job.get("engine", "tavily"), "layer": job["layer"], "query": job["query"],
                              "country": job.get("country"), "type_hint": job["type_hint"],
                              "category": job.get("category"), "role": job.get("role"), "lang": job["lang"],
                              "event_id": job.get("event_id"), "result_url": r["url"]}})
    ranked = sorted(seen.items(), key=lambda kv: (-(len(kv[1]["queries"]) + 2 * focus(kv[0])), kv[1]["order"]))
    candidates, per_layer = [], Counter()
    for site, info in ranked:
        if len(candidates) >= max_new:
            break
        room = [c for layer, c in info["by_layer"].items()
                if not layer_caps or per_layer[layer] < layer_caps.get(layer, max_new)]
        if not room:
            continue
        cand = room[0]
        per_layer[cand["found_via"]["layer"]] += 1
        cand["found_via"].update(hits=len(info["queries"]), focus=focus(site))
        cand["prefilter"] = cand["found_via"]["layer"] != "section"  # first stage; a section is already narrowed
        candidates.append(cand)
    return candidates, found, done, {"queries": used, "failed": failed}


def reissued(candidates, known):
    """Known addresses issued again. A defence section of a general site is a new address on a known host
    (cycle 3 counted 14 sections as re-issued sites); it is re-issued only if that exact section is known."""
    known_urls = {k for k in known if "/" in k}
    return sum((A.registry.canonical_url(c["url"]) in known_urls) if c["found_via"]["layer"] == "section"
               else (A.site_of(A.registry.host_of(c["url"])) in known) for c in candidates)


def measure_cycle(candidates, found, results, known=()):
    """A1SD acceptance per cycle: >= 10 countries and >= 5 types among candidates, no known address re-issued,
    accepted share >= 30%; plus saturation (share of already known among everything found)."""
    countries = {c["found_via"].get("country") for c in candidates if c["found_via"].get("country")}
    countries |= {r.get("country") for r in results if r.get("country") and r.get("country") != "international"}
    types = {r.get("type") for r in results if r.get("type")}
    general = [r for r in results if r["decision"] == "general"]
    decided = [r for r in results if r["decision"] not in ("retry", "general")]
    accepted = [r for r in decided if r["decision"] == "accept"]
    by_layer = Counter(c["found_via"]["layer"] for c in candidates)
    acc_layer = Counter()
    for r in accepted:
        acc_layer[r.get("found_via_layer") or "?"] += 1
    return {
        "candidates": len(candidates), "countries": sorted(countries), "n_countries": len(countries),
        "types": sorted(types), "n_types": len(types),
        "decisions": dict(Counter(r["decision"] for r in results)),
        "accepted_share": round(len(accepted) / len(decided), 3) if decided else None,
        # transparency: general outlets are set aside before the share (D-20261118-A1SD-RETRO-PLAN-G2)
        "general_outlets": len(general),
        "accepted_share_incl_general": round(len(accepted) / (len(decided) + len(general)), 3)
        if decided or general else None,
        "disputed_for_owner": sum(r["decision"] == "disputed" for r in results),
        "known_reissued": reissued(candidates, known),
        "saturation": round(sum(f["known"] for f in found) / len(found), 3) if found else None,
        "found_by_layer": dict(Counter(f["layer"] for f in found)), "candidates_by_layer": dict(by_layer),
        "candidates_by_engine": dict(Counter(c["found_via"].get("channel") for c in candidates)),
        "decisions_by_engine": {f"{e}:{d}": n for (e, d), n in Counter(
            (r.get("found_via_engine"), r["decision"]) for r in results).items()},
        "a1sd_cycle_ok": len(countries) >= 10 and len(types) >= 5 and bool(decided) and not reissued(candidates, known)
                         and len(accepted) / len(decided) >= 0.30,
    }


def grid(conn):
    """Accepted sources by country x type x language in the shadow registry (design measure b)."""
    rows = conn.execute("SELECT country, type, language, count(*) FROM source_state WHERE status='accepted' "
                        "GROUP BY 1, 2, 3 ORDER BY 1, 2, 3").fetchall()
    return [{"country": c, "type": t, "language": lang, "n": n} for c, t, lang, n in rows]


def coverage(control_events, our_hosts, exclude_hosts=()):
    """Design measure a: share of control events found by at least one of our sources; the control set's own
    publishers (top media of set A) do not count as ours."""
    ours = {A.site_of(h) for h in our_hosts} - {A.site_of(h) for h in exclude_hosts}
    hit = [e for e in control_events if {A.site_of(h) for h in e["hosts"]} & ours]
    return {"events": len(control_events), "found": len(hit),
            "share": round(len(hit) / len(control_events), 3) if control_events else None}


def network_parts():
    """Budget and HTTP for a live cycle. observer goes first: it puts Codex's collectors package on the path
    (cycle 2's first start fell here with ModuleNotFoundError before any request)."""
    import observer  # noqa: F401
    from codex_discovery.radars import Budget
    from transport_ext import HTTP
    return Budget, HTTP


def network_up(waits=3, pause=60, hosts=("www.wikipedia.org", "www.google.com", "api.github.com")):
    """Our own network: at least two well-known names resolve. Checked up to `waits` times, `pause` s apart."""
    import socket
    for i in range(waits):
        ok = 0
        for h in hosts:
            try:
                socket.getaddrinfo(h, 443)
                ok += 1
            except OSError:
                pass
        if ok >= 2:
            return True
        if i < waits - 1:
            time.sleep(pause)
    return False


def execute(todo, search, http, conn, known, raw=None, use_feedsearch=False, network_pause=60, extra=(), recheck=()):
    """One cycle without files: search -> candidates -> observe (stops at a cap or a network outage) -> admit."""
    import observer as O
    candidates, found, done, trace = collect(todo, search, known, sum(LIMITS.get(e, 0) for e in ("tavily", "gemini", "gdelt", "wikidata")),
                                             LIMITS["new"], NEW_CAPS, raw)
    if isinstance(search, Engines):
        trace.update(engines_used=dict(search.used), engines_off=sorted(search.off))
    candidates = candidates + list(extra)  # company websites of our base, on-hold sources due again (no search)
    observations, streak, queue, again = [], 0, list(candidates), []
    while queue:
        cand = queue.pop(0)
        try:
            o = O.observe(cand, http, use_feedsearch=use_feedsearch)
        except O.Problem as exc:
            trace["stop"] = str(exc)
            break
        observations.append(o)
        streak = streak + 1 if o["fetch"].get("status") == "connect_error" else 0
        if streak >= 5:
            # cycle 4: a short outage of our own network stopped the cycle. Check our network; if it is up, the
            # five go to the end of the queue once; if it stays down after 3 checks a minute apart, stop.
            if not network_up(waits=3, pause=network_pause):
                trace["stop"] = "NETWORK_DOWN"
                break
            failed = observations[-5:]
            del observations[-5:]
            again += [f["candidate"] for f in failed if f["candidate"] not in again]
            queue += [c for c in again if c not in queue][: len(failed)]
            trace["network_recovered"] = trace.get("network_recovered", 0) + 1
            streak = 0
    counts, results = A.admit(conn, observations, recheck=set(recheck))
    layer_of = {A.registry.canonical_url(c["url"]): c["found_via"] for c in candidates}
    for r in results:
        fv = layer_of.get(A.registry.canonical_url(r["candidate_url"])) or {}
        r["found_via_layer"], r["found_via_engine"] = fv.get("layer"), fv.get("channel")
    return candidates, found, done, trace, observations, counts, results


# Entry point in FIELD: run_daily.py (state in Supabase schema source_discovery). The Mac shadow runner of
# cycles 1-5 stays in BOHUN-local EXP-033/source-admission-shadow-20261118.
