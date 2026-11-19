"""A1SD-02 search by the approved principle (D-20261118-A1SD-SEARCH-DESIGN-G2): what to ask the search engine.

Three kinds of jobs, one Tavily query each, built without the network:
- skeleton: official sources of a country by a fixed template (6 roles), once per country;
- layer1:   category x country x language (country language and English), categories from our base;
- layer3:   "who else wrote about it" - the title of an event from our base, its known publishers excluded.
The queue orders jobs by gap (cells and topics with the fewest sources/events first) and cuts them by the cycle quota.
Layer 2 (company websites from the base) already exists as a pool and is not planned here.
"""
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CATEGORIES = HERE / "categories.json"

# Official skeleton (design): ministry, procurement agency, parliament committee, export control, defence funds,
# statistics/registries. English queries: official bodies are usually indexed with English names too.
SKELETON = {
    "ministry": ("{country} ministry of defence official news", "agency"),
    "procurement_agency": ("{country} defence procurement agency official", "procurement"),
    "parliament": ("{country} parliament defence committee", "agency"),
    "export_control": ("{country} arms export control authority", "agency"),
    "defence_funds": ("{country} defence innovation fund official", "grants"),
    "registry": ("{country} defence industry statistics register official", "company_registry"),
}
WINDOW = 9          # how many top-priority categories a cycle spreads over the countries
PER_COUNTRY = 3     # categories per country per cycle (x2 languages = 6 queries)


def load(path=CATEGORIES):
    cat = json.loads(Path(path).read_text(encoding="utf-8"))
    langs = {c["lang"] for c in cat["countries"].values()} | {"en"}
    for cid, c in cat["categories"].items():  # a missing translation stops at load, not mid-cycle
        missing = langs - {k for k, v in c["terms"].items() if v.strip()}
        if missing:
            raise ValueError(f"categories.json: {cid} has no terms for {sorted(missing)}")
    return cat


def job_key(job):
    key = "|".join(str(job.get(k, "")) for k in ("layer", "country", "category", "role", "lang", "event_id"))
    return key + (f"|{job['site']}" if job.get("site") else "")  # keys of cycles 1-2 stay unchanged


def skeleton_jobs(cat, countries):
    jobs = []
    for role, (template, type_hint) in SKELETON.items():  # role-major: a partial quota still covers many countries
        for cc in countries:
            c = cat["countries"][cc]
            jobs.append({"layer": "skeleton", "country": cc, "role": role, "lang": "en", "type_hint": type_hint,
                         "query": template.format(country=c["name_en"]), "topic": "general", "time_range": None,
                         "max_results": 5})
    return jobs


def priority(cat, accepted_by_cell=None):
    """Categories ordered by gap: technology topics by the number of distinct events in our base (fewest first),
    deal/process categories by known events; the two lists are interleaved so a cycle asks about both."""
    acc = accepted_by_cell or {}

    def key(item):
        cid, c = item
        seen = sum(n for (cc, cat_id), n in acc.items() if cat_id == cid)
        return (seen, c["base_events"] if c["base_events"] is not None else 0, cid)

    tech = sorted((x for x in cat["categories"].items() if x[1]["kind"] == "tech"), key=key)
    deal = sorted((x for x in cat["categories"].items() if x[1]["kind"] == "deal"), key=key)
    out = []
    for i in range(max(len(tech), len(deal))):
        out += [x[0] for x in (tech[i:i + 1] + deal[i:i + 1])]
    return out


def categories_for(cat, order, country, index, asked=()):
    """PER_COUNTRY categories for a country: a window over the top of the priority list, shifted per country so the
    countries together cover the whole window; a category limited to some countries is skipped elsewhere, and one
    already asked for this country in an earlier cycle is skipped, so the next cycle moves down the list."""
    top = [c for c in order if country in cat["categories"][c].get("countries", [country]) and (country, c) not in asked]
    if not top:
        return []
    window = top[:WINDOW]
    want = PER_COUNTRY * (2 if cat["countries"][country]["lang"] == "en" else 1)  # English-only country: 2x categories
    start = (index * PER_COUNTRY) % len(window)
    picked = []
    for k in range(len(window)):
        c = window[(start + k) % len(window)]
        if c not in picked:
            picked.append(c)
        if len(picked) == want:
            break
    return picked


def layer1_jobs(cat, countries, accepted_by_cell=None, done=()):
    order = priority(cat, accepted_by_cell)
    asked = {tuple(k.split("|")[1:3]) for k in done if k.startswith("layer1|")}
    rows = []
    for i, cc in enumerate(countries):
        c = cat["countries"][cc]
        langs = [c["lang"]] if c["lang"] == "en" else [c["lang"], "en"]
        for slot, cid in enumerate(categories_for(cat, order, cc, i, asked)):
            for lang in langs:
                name = c["name_local"] if lang == c["lang"] else c["name_en"]
                rows.append((slot, lang != c["lang"], i, {
                    "layer": "layer1", "country": cc, "category": cid, "lang": lang,
                    "type_hint": cat["categories"][cid]["default_type"],
                    "query": f'{cat["categories"][cid]["terms"][lang]} {name}', "topic": "news", "time_range": "month",
                    "max_results": 10}))
    # slot-major, then language, then country: the first 24 queries already touch all 12 countries
    return [r[3] for r in sorted(rows, key=lambda r: (r[0], r[1], r[2]))]


def norm_title(title):
    return re.sub(r"\W+", " ", (title or "").casefold()).strip()


def layer3_jobs(cat, events, n=50, done=()):
    """Events from our base -> "who else wrote". Duplicate events (same title) count once; topics round-robin with
    the gap topics first; the publishers we already have for the event are excluded from its results."""
    gap = {c["base_topic"]: c["base_events"] for c in cat["categories"].values() if c["base_topic"]}
    by_topic, seen = {}, set()
    asked = {k.split("|")[5] for k in done if k.startswith("layer3|")}
    for e in events:
        if str(e["id"]) in asked:  # cycle 3: without this the first 50 (already asked) were picked again
            continue
        t = norm_title(e["title"])
        if not t or t in seen:
            continue
        seen.add(t)
        by_topic.setdefault(e.get("topic") or "", []).append(e)
    topics = sorted(by_topic, key=lambda t: (gap.get(t, 10 ** 6), t))
    picked = []
    while len(picked) < n and any(by_topic.values()):
        for t in topics:
            if by_topic[t] and len(picked) < n:
                picked.append(by_topic[t].pop(0))
    return [{"layer": "layer3", "event_id": e["id"], "topic_slug": e.get("topic"), "lang": "en", "type_hint": "media",
             "query": e["title"][:200], "topic": "news", "time_range": "month", "max_results": 10,
             "exclude_hosts": sorted(set(e.get("known_hosts") or []))} for e in picked]


def plan_cycle(jobs_by_layer, quotas, done=()):
    """Jobs for one cycle: each layer gets its quota of not-yet-done jobs, in the layer's priority order."""
    done = set(done)
    out = []
    for layer, quota in quotas.items():
        todo = [j for j in jobs_by_layer.get(layer, []) if job_key(j) not in done]
        out += todo[:quota]
    return out


# Decision 2 of the 18.11 review: a general outlet (or an official site that writes about everything) may still have a
# defence section/tag/feed - that section is a source of its own. One query per site, restricted to its domain.
SECTION_TERM = {"en": "defence", "uk": "оборона", "pl": "obronność", "de": "Verteidigung", "fr": "défense",
                "es": "defensa", "it": "difesa", "nl": "defensie", "sv": "försvar", "fi": "puolustus", "cs": "obrana",
                "ro": "apărare"}
OFFICIAL = re.compile(r"(^|\.)(gov|mil|gouv|gob|bund|mod)\.|\.gov(\.[a-z]{2})?$|\.mil$|parl|senat|riksdag|regering|"
                      r"bundestag|sejm|rada|valtioneuvosto|eduskunta|vlada|assemblee|camera|senado")


def section_jobs(general_sites):
    """general_sites: [{"site", "language"}] from the shadow registry (status general_outlet).
    Official/parliament sites first, then the rest in registry order."""
    ordered = sorted(general_sites, key=lambda g: (0 if OFFICIAL.search(g["site"]) else 1))
    jobs = []
    for g in ordered:
        lang = g.get("language") if g.get("language") in SECTION_TERM else "en"
        jobs.append({"layer": "section", "site": g["site"], "lang": lang, "type_hint": "media",
                     "query": SECTION_TERM[lang], "include_domains": [g["site"]], "topic": "general",
                     "time_range": None, "max_results": 5})
    return jobs


# Cycle 4 (19.11): without the official skeleton a cycle finds almost only media (2 types). This layer asks every
# cycle for the kinds of sources a cycle must cover (spec A1SD: >= 5 types per cycle). Type-major, countries rotate.
TYPE_QUERIES = {
    "procurement": "{country} defence procurement tenders official portal",
    "exhibition": "{country} defence and security exhibition trade fair",
    "association": "{country} defence industry association news",
    "research": "{country} defence research institute news",
    "company_news": "{country} defence company newsroom press releases",
    "grants": "{country} defence innovation funding call grants",
}


GDELT_COUNTRY = {"UA": "ukraine", "PL": "poland", "DE": "germany", "FR": "france", "ES": "spain", "IT": "italy",
                 "NL": "netherlands", "SE": "sweden", "FI": "finland", "CZ": "czechrepublic", "RO": "romania",
                 "GB": "unitedkingdom"}
WIKIDATA_COUNTRY = {"UA": "Q212", "PL": "Q36", "DE": "Q183", "FR": "Q142", "ES": "Q29", "IT": "Q38", "NL": "Q55",
                    "SE": "Q34", "FI": "Q33", "CZ": "Q213", "RO": "Q218", "GB": "Q145"}


def gdelt_jobs(cat, countries, done=()):
    """Open news base GDELT: English category term + source country (GDELT searches its translated coverage).
    Gap categories first, country-major rotation; a job already asked is skipped."""
    asked = {k for k in done if k.startswith("gdelt|")}
    order = priority(cat)
    rows = []
    for slot, cid in enumerate(order):
        for i, cc in enumerate(countries):
            if cc not in GDELT_COUNTRY or cc not in cat["categories"][cid].get("countries", [cc]):
                continue
            job = {"layer": "gdelt", "engine": "gdelt", "country": cc, "category": cid, "lang": "en",
                   "type_hint": cat["categories"][cid]["default_type"], "query": cat["categories"][cid]["terms"]["en"],
                   "gdelt_country": GDELT_COUNTRY[cc]}
            if job_key(job) not in asked:
                rows.append((slot, i, job))
    return [r[2] for r in sorted(rows, key=lambda r: (r[0], r[1]))]


def wikidata_jobs(countries, done=()):
    """Official bodies of a country (ministries, agencies, parliament bodies) with their websites, once per country."""
    asked = {k for k in done if k.startswith("wikidata|")}
    jobs = [{"layer": "wikidata", "engine": "wikidata", "country": cc, "lang": "en", "type_hint": "agency",
             "query": f"official defence bodies {cc}", "qid": WIKIDATA_COUNTRY[cc]}
            for cc in countries if cc in WIKIDATA_COUNTRY]
    return [j for j in jobs if job_key(j) not in asked]


def type_jobs(cat, countries, done=()):
    """One job per (source type, country); not-yet-asked first, type-major so a partial quota covers all types."""
    asked = {k for k in done if k.startswith("types|")}
    rows = []
    for t, template in TYPE_QUERIES.items():
        for i, cc in enumerate(countries):
            job = {"layer": "types", "country": cc, "role": t, "lang": "en", "type_hint": t,
                   "query": template.format(country=cat["countries"][cc]["name_en"]), "topic": "general",
                   "time_range": None, "max_results": 8}
            if job_key(job) not in asked:
                rows.append((i, list(TYPE_QUERIES).index(t), job))
    # round-robin: country 0 of every type, then country 1 of every type ... -> every type appears early
    return [r[2] for r in sorted(rows, key=lambda r: (r[0], r[1]))]
