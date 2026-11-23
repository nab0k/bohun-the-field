"""A1SD-02 daily cloud run (D-20261119-A1SD-DAILY-CLOUD): one bounded search cycle in the shadow registry.

  python -B collectors/source_registry/search/run_daily.py [--dry-run]

State lives in Supabase schema source_discovery (pg_sync). Search: Gemini with Google Search grounding
(GEMINI_SEARCH_API_KEY) and Tavily when TAVILY_API_KEY is set. Nothing goes to product tables, collection or
Inoreader. The repository is public: stdout carries counts only; candidates, decisions and titles stay in the database.
"""
import argparse
import datetime as dt
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import admission as A  # noqa: E402
import health as H  # noqa: E402
import learning as L  # noqa: E402
import pg_sync  # noqa: E402
import plan as P  # noqa: E402
import providers as PR  # noqa: E402
import search_cycle as C  # noqa: E402
from eligibility import owner_accepted  # noqa: E402


OPEN_LAYERS = ("gdelt", "wikidata", "similar")  # layers whose engine is fixed (not Gemini/Jina/Tavily)
# A1SD-03 (owner 19.11 "Да, подключай"): Jina ~10M free tokens once, 10 000 a search -> ~1 000 searches: 10 a day,
# added to the types and layer1 layers; Exa free credits, ~$0.004 a call: 15 "similar to accepted" a day
JINA_SHARE = {"types": 5, "layer1": 5}


def tavily_left(quotas, gemini, jina):
    return sum(v for k, v in quotas.items() if k not in OPEN_LAYERS) - sum(gemini.values()) - sum(jina.values())


def engine_shares(quotas, gemini_total, gemini_layers, jina_share):
    """20.11: the learning moves queries between layers; the engines' daily free limits must not move with them (the
    dry run gave Gemini 49 of its 40). Gemini gets exactly gemini_total spread over the layers it serves, in proportion
    to their new quotas and leaving each layer's Jina share; Jina keeps its share; Tavily takes the rest."""
    jina = {k: min(v, quotas.get(k, 0)) for k, v in jina_share.items()}
    room = {l: max(0, quotas.get(l, 0) - jina.get(l, 0)) for l in gemini_layers}
    total_room = sum(room.values())
    want = min(gemini_total, total_room)
    gemini = {l: (want * r // total_room if total_room else 0) for l, r in room.items()}
    for l in sorted(room, key=lambda l: room[l] - gemini[l], reverse=True):  # rounding remainder, within room
        if sum(gemini.values()) >= want:
            break
        if gemini[l] < room[l]:
            gemini[l] += 1
    return gemini, jina


def daily_limits(tavily, jina=False, exa=False):
    """Free quotas: Gemini grounding ~5 000/month -> 40/day; Tavily ~1 000/month -> 20/day when its key exists."""
    if tavily:
        quotas = {"types": 18, "layer1": 24, "layer3": 10, "section": 8}
        gemini = {"types": 14, "layer1": 16, "layer3": 10, "section": 0}
    else:
        quotas = {"types": 16, "layer1": 16, "layer3": 8, "section": 0}
        gemini = dict(quotas)
    # open bases, no key (D-20261119-A1SD-OPEN-BASES). GDELT answered 429 from the Mac and from GitHub (cloud run 3,
    # 19.11): off by default, SEARCH_GDELT_PER_DAY turns it back on.
    # A1SD-03 wave 1: Wikidata officials and arms/aerospace companies for 50 countries of Europe, 6 s apart; its new
    # sites have their own cap so the search layers keep theirs
    quotas.update(gdelt=int(os.environ.get("SEARCH_GDELT_PER_DAY", "0")),
                  wikidata=int(os.environ.get("SEARCH_WIKIDATA_PER_DAY", "20")))
    jina_share = dict(JINA_SHARE) if jina else {}
    for layer, n in jina_share.items():
        quotas[layer] += n
    quotas["similar"] = int(os.environ.get("SEARCH_EXA_PER_DAY", "15")) if exa else 0
    new_search = int(os.environ.get("SEARCH_NEW_LIMIT", "54"))
    limits = {"tavily": tavily_left(quotas, gemini, jina_share), "gemini": sum(gemini.values()),
              "jina": sum(jina_share.values()), "exa": quotas["similar"], "gdelt": quotas["gdelt"],
              "wikidata": quotas["wikidata"], "http": int(os.environ.get("SEARCH_HTTP_LIMIT", "600")),
              "new": new_search + WIKIDATA_NEW}
    return limits, quotas, gemini, caps_for(quotas, new_search), jina_share


WIKIDATA_NEW = int(os.environ.get("SEARCH_WIKIDATA_NEW", "12"))


def caps_for(quotas, new_search):
    """New-site caps per layer: the search layers share new_search by their quotas; Wikidata has its own."""
    search = {k: v for k, v in quotas.items() if k != "wikidata"}
    total = sum(search.values()) or 1
    caps = {k: round(new_search * v / total) for k, v in search.items()}
    caps["wikidata"] = WIKIDATA_NEW if quotas.get("wikidata") else 0
    return caps


def accepted_sites(conn):
    """Accepted sources of the shadow registry as (site, country), for "similar" (one site once)."""
    out, seen = [], set()
    for url, country in conn.execute("SELECT canonical_url, country FROM source_state WHERE status='accepted' "
                                     "ORDER BY since DESC").fetchall():
        site = A.site_of(A.registry.host_of(url) or "")
        if site and site not in seen:
            seen.add(site)
            out.append((site, country if country and country != "international" else None))
    return out


AUDIT_PER_DAY = int(os.environ.get("SEARCH_AUDIT_PER_DAY", "25"))
PREVIOUS_RULES = "a2sa-shadow-0.9"


def review_candidate(conn, url, channel, layer):
    candidate = {"url": url, "pool_group": layer, "prefilter": True,
                 "found_via": {"channel": layer, "layer": layer}}
    row = conn.execute("SELECT canonical_url, delivery FROM source_state WHERE channel_id=?", (channel,)).fetchone()
    if row and row[1] in ("feed", "page"):
        candidate.update(url=row[0], expected_channel_id=channel, expected_delivery=row[1])
    return candidate


def audit_candidates(conn, n=AUDIT_PER_DAY):
    """0.7 (owner 19.11 "Да, чисти"): accepted sources decided under older rules or without the judge's verdict are
    checked again, oldest first; the owner's own accepts stay. Returns [(candidate, old_channel)]."""
    rows = conn.execute(
        "SELECT s.candidate_url, s.channel_id, d.rules_version, d.record_json, d.reasons_json FROM candidate_seen s "
        "JOIN admission_decision d ON d.decision_id=(SELECT max(decision_id) FROM admission_decision x "
        "WHERE x.candidate_url=s.candidate_url) WHERE s.last_decision='accept' ORDER BY s.decided_at").fetchall()
    # 20.11: 86 accepted sources were seeded from the Mac cycles with no candidate_seen row and no decision; they are
    # audited through their site root, oldest first, before the rest
    seeded = conn.execute(
        "SELECT canonical_url, channel_id, delivery FROM source_state s WHERE status='accepted' AND NOT EXISTS "
        "(SELECT 1 FROM candidate_seen c WHERE c.channel_id=s.channel_id) AND NOT EXISTS "
        "(SELECT 1 FROM admission_decision d WHERE d.channel_id=s.channel_id) ORDER BY since").fetchall()
    out, taken = [], set()
    for url, channel, delivery in seeded:
        host = A.registry.host_of(url)
        review_url = url if delivery in ("feed", "page") else (f"https://{host}/" if host else None)
        if review_url and review_url not in taken:
            taken.add(review_url)
            out.append((review_url, channel))
    for url, channel, rules, record, reasons in rows:
        if owner_accepted(json.loads(reasons or "[]")) or (
            rules in (A.RULES_VERSION, PREVIOUS_RULES) and json.loads(record or "{}").get("judge")
        ):
            continue
        if url not in taken:
            taken.add(url)
            out.append((url, channel))
    candidates = {}
    for url, channel in out:
        candidate = review_candidate(conn, url, channel, "audit")
        candidate["decision_url"] = url
        candidates.setdefault(candidate["url"], (candidate, channel))
    return list(candidates.values())[:n]


COMPANY_PER_DAY = int(os.environ.get("SEARCH_COMPANY_PER_DAY", "12"))
RECHECK_PER_DAY = int(os.environ.get("SEARCH_RECHECK_PER_DAY", "8"))
RECHECK_AFTER_DAYS = 14
# 0.6: a site held only because the judge gave no verdict waits for the next daily run, not two weeks (owner 19.11
# "Да, ускорь"); the judge is usually back by then
NO_JUDGE_AFTER_HOURS = 6


def company_candidates(ctx, known, n=COMPANY_PER_DAY):
    """Layer 2 of the design: websites of companies and research bodies in our base, not yet seen, a few a day.
    No search query is spent; the first stage still sets aside a site that is not about defence."""
    out, taken = [], set()
    for host in sorted(ctx["company"] | ctx["rnd"]):
        site = A.site_of(host)
        # cloud run 1: adding these to `known` made the measure count all 12 as re-issued known addresses
        if not site or site in known or site in taken or site in C.AGGREGATORS:
            continue
        taken.add(site)
        out.append({"url": f"https://{host}/", "pool_group": "layer2", "prefilter": True,
                    "found_via": {"channel": "base_company", "layer": "layer2",
                                  "country": ctx["company_country"].get(host), "type_hint": "company_news"}})
        if len(out) >= n:
            break
    return out


def recheck_candidates(conn, n=RECHECK_PER_DAY, days=RECHECK_AFTER_DAYS, no_judge_hours=NO_JUDGE_AFTER_HOURS):
    """On-hold sources (stale, too few titles, undated) whose last check is older than `days`: checked again.
    Sites held for want of a judge verdict (no_judge) come back after `no_judge_hours` and go first.
    0.8 (owner 20.11 "решай сам"): sources left disputed by older rules come first and are decided again."""
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = (now - dt.timedelta(days=days)).isoformat()
    quick = (now - dt.timedelta(hours=no_judge_hours)).isoformat()
    disputed = [u for (u,) in conn.execute("SELECT candidate_url FROM candidate_seen WHERE last_decision='disputed' "
                                           "ORDER BY decided_at LIMIT ?", (n,)).fetchall()]
    held = [u for (u,) in conn.execute(
        "SELECT candidate_url FROM (SELECT s.candidate_url, s.decided_at, (SELECT d.reasons_json FROM admission_decision d "
        " WHERE d.candidate_url=s.candidate_url ORDER BY d.decision_id DESC LIMIT 1) reasons FROM candidate_seen s "
        " WHERE s.last_decision='hold') WHERE reasons NOT LIKE '%no_news_site%' AND "
        "(decided_at < ? OR (reasons LIKE '%no_judge%' AND decided_at < ?)) "
        "ORDER BY (reasons LIKE '%no_judge%') DESC, decided_at LIMIT ?",
        (cutoff, quick, n)).fetchall()]
    # 0.9 (A1SD-04): sites set aside as general by the keyword count alone are judged again under the new rules
    general = [u for (u,) in conn.execute(
        "SELECT s.candidate_url FROM candidate_seen s JOIN admission_decision d ON d.decision_id=(SELECT max(decision_id) "
        "FROM admission_decision x WHERE x.candidate_url=s.candidate_url) WHERE s.last_decision='general' "
        "AND d.reasons_json LIKE '%topic_below_threshold%' AND d.rules_version NOT IN (?,?) "
        "ORDER BY s.decided_at LIMIT ?",
        (A.RULES_VERSION, PREVIOUS_RULES, max(n // 3, 1))).fetchall()]
    urls = (disputed + [u for u in general if u not in disputed]
            + [u for u in held if u not in disputed and u not in general])[:n]
    # prefilter: a recheck goes through the judge again (0.6: a no_judge hold must not pass on keywords alone)
    candidates = {}
    for url in urls:
        row = conn.execute("SELECT channel_id FROM candidate_seen WHERE candidate_url=?", (url,)).fetchone()
        candidate = review_candidate(conn, url, row[0] if row else None, "recheck")
        candidate["decision_url"] = url
        candidates.setdefault(candidate["url"], candidate)
    return list(candidates.values())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="read the state and plan; no search, no site, no write")
    a = ap.parse_args()
    tavily = bool(os.environ.get("TAVILY_API_KEY", "").strip())
    jina, exa = (bool(os.environ.get(k, "").strip()) for k in ("JINA_API_KEY", "EXA_API_KEY"))
    C.LIMITS, C.QUOTAS, C.GEMINI_SHARE, C.NEW_CAPS, jina_share = daily_limits(tavily, jina, exa)
    new_search = C.LIMITS["new"] - WIKIDATA_NEW
    started = dt.datetime.now(dt.timezone.utc)
    run_id = "gh-" + os.environ.get("GITHUB_RUN_ID", "local") + "-" + started.strftime("%Y%m%dT%H%M")
    with pg_sync.connect() as pg, tempfile.TemporaryDirectory() as tmp:
        # A6SH-02: health and usefulness first, so failing/silent sources leave the accepted list before the run
        health_details = {}
        health = {} if a.dry_run else H.update(pg, diagnostics=health_details)
        useful = H.useful_by_layer(pg)
        conn, ctx = pg_sync.load(pg, Path(tmp) / "registry.sqlite")
        A.set_base(ctx["company"], ctx["rnd"])
        cat = P.load()
        countries = list(cat["countries"])
        europe = P.world()  # 20.11 waves 2-4: Europe, Asia, the Americas and Oceania (133 countries)
        # A1SD-03: learn from earlier runs - quotas by yield (or defaults / reset), gaps first, sleeping cells last
        C.QUOTAS, C.GEMINI_SHARE, learned = L.adapt(C.QUOTAS, C.GEMINI_SHARE, ctx["learning"],
                                                    [(r["at"], r["share"]) for r in ctx["runs"]], useful=useful)
        C.NEW_CAPS = caps_for(C.QUOTAS, new_search)
        gemini_total = sum(daily_limits(tavily, jina, exa)[2].values())
        C.GEMINI_SHARE, jina_share = engine_shares(C.QUOTAS, gemini_total, [l for l, v in daily_limits(tavily, jina, exa)[2].items() if v],
                                                   jina_share)
        C.LIMITS["gemini"] = sum(C.GEMINI_SHARE.values())
        C.LIMITS["jina"] = sum(jina_share.values())
        C.LIMITS["tavily"] = tavily_left(C.QUOTAS, C.GEMINI_SHARE, jina_share)
        asleep = L.sleeping_cells(ctx["learning"])
        gaps = lambda js: L.order_by_gaps(js, ctx["accepted_by_country"], ctx["accepted_by_type"], asleep)  # noqa: E731
        names = {cc: c["name_en"] for cc, c in europe.items()}
        cat1 = P.with_europe(cat, europe)  # layer 1 over the world; the 12 original countries keep their languages
        jobs = {"layer1": gaps(P.layer1_jobs(cat1, list(cat1["countries"]), None, ctx["done"])),
                "layer3": P.layer3_jobs(cat, ctx["events"], 50, ctx["done"]),
                "section": P.section_jobs(C.general_sites(conn)) if tavily else [],
                "types": P.weight_types(gaps(P.type_jobs(cat, list(europe), ctx["done"], names=names))),
                "gdelt": P.gdelt_jobs(cat, countries, ctx["done"]),
                "wikidata": gaps(P.wikidata_jobs(list(europe), ctx["done"])),
                "similar": gaps(P.similar_jobs(accepted_sites(conn), ctx["done"])) if exa else []}
        todo = C.assign_engines(P.plan_cycle(jobs, C.QUOTAS, ctx["done"]), C.GEMINI_SHARE, jina_share)
        plan = {"planned": dict(Counter(j["layer"] for j in todo)),
                "engines": dict(Counter(j["engine"] for j in todo)), "limits": C.LIMITS,
                "rules_version": A.RULES_VERSION, "categories_version": cat["version"],
                "learning": {**learned, "quotas": C.QUOTAS, "asleep": len(asleep)},
                "state_rows": ctx["state_rows"], "events": len(ctx["events"])}
        print(json.dumps({"run": run_id, **plan, "health": health}))
        if a.dry_run:
            return
        known = C.known_hosts(conn, ctx["known"])
        Budget, HTTP = C.network_parts()
        import judge as J
        import observer as O
        O.JUDGE = J.Judge() if os.environ.get("SEARCH_JUDGE", "1") == "1" else None
        budget = Budget({"max_requests": C.LIMITS["http"], "max_seconds": 1800, "max_bytes": 200_000_000})
        http = HTTP(budget, enabled=True)
        engines = C.Engines(tavily=PR.tavily_api, gemini=lambda job: PR.gemini_grounded(job["query"]))
        t0 = time.time()
        again = recheck_candidates(conn)
        audit = audit_candidates(conn)
        extra = company_candidates(ctx, known) + again + [c for c, _ in audit]
        cands, found, done, trace, _obs, counts, results = C.execute(
            todo, engines, http, conn, known, use_feedsearch=True, extra=extra,
            recheck={A.registry.canonical_url(c["decision_url"]) for c in again},
            audit={A.registry.canonical_url(c["decision_url"]): ch for c, ch in audit})
        trace.update(http_requests=budget.requests, seconds=round(time.time() - t0),
                     engines_used=dict(engines.used), engines_off=sorted(engines.off),
                     engine_errors=dict(engines.errors),
                     judge_used=dict(O.JUDGE.used) if O.JUDGE else {}, judge_errors=dict(O.JUDGE.errors) if O.JUDGE else {},
                     judge_off=sorted(O.JUDGE.off) if O.JUDGE else [])
        job_stats = trace.pop("job_stats", {})
        trace["health_diagnostics"] = health_details
        measure = C.measure_cycle(cands, found, results, known)
        measure["disputed_total"] = len(A.disputed_batch(conn))
        # A1SD acceptance: three successful runs in a row, not three days (owner 19.11)
        measure["a1sd_streak"] = L.streak([r["ok"] for r in ctx["runs"]], measure["a1sd_cycle_ok"])
        run = {"run_id": run_id, "started_at": started.isoformat(),
               "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(), "where_run": "github" if
               os.environ.get("GITHUB_RUN_ID") else "local", "plan": plan, "trace": trace,
               "measure": measure, "done_jobs": done,
               "learning": L.rows_for_run(run_id, job_stats, results),
               "audited_channels": [ch for _, ch in audit if ch]}
        written = pg_sync.save(pg, conn, ctx, run)
    # public log: counts only
    print(json.dumps({"trace": {k: trace.get(k) for k in ("queries", "failed", "http_requests", "seconds",
                                                          "engines_used", "engines_off", "engine_errors", "stop",
                                                          "network_recovered", "judge_used", "judge_errors",
                                                          "judge_off")},
                      "decisions": counts, "written": written,
                      "measure": {k: measure.get(k) for k in ("candidates", "n_countries", "n_types", "accepted_share",
                                                              "accepted_share_search", "accepted_share_layer2",
                                                              "accepted_share_incl_general", "disputed_for_owner",
                                                              "known_reissued", "saturation", "a1sd_cycle_ok",
                                                              "a1sd_streak", "audit")},
                      "learning": {k: plan["learning"].get(k) for k in ("mode", "window_jobs", "asleep", "quotas")}}))


if __name__ == "__main__":
    main()
