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
import pg_sync  # noqa: E402
import plan as P  # noqa: E402
import providers as PR  # noqa: E402
import search_cycle as C  # noqa: E402


def daily_limits(tavily):
    """Free quotas: Gemini grounding ~5 000/month -> 40/day; Tavily ~1 000/month -> 20/day when its key exists."""
    if tavily:
        quotas = {"types": 18, "layer1": 24, "layer3": 10, "section": 8}
        gemini = {"types": 14, "layer1": 16, "layer3": 10, "section": 0}
    else:
        quotas = {"types": 16, "layer1": 16, "layer3": 8, "section": 0}
        gemini = dict(quotas)
    limits = {"tavily": sum(quotas.values()) - sum(gemini.values()), "gemini": sum(gemini.values()),
              "http": int(os.environ.get("SEARCH_HTTP_LIMIT", "500")), "new": int(os.environ.get("SEARCH_NEW_LIMIT", "40"))}
    total = sum(quotas.values())
    caps = {k: round(limits["new"] * v / total) for k, v in quotas.items()}
    return limits, quotas, gemini, caps


COMPANY_PER_DAY = int(os.environ.get("SEARCH_COMPANY_PER_DAY", "12"))
RECHECK_PER_DAY = int(os.environ.get("SEARCH_RECHECK_PER_DAY", "8"))
RECHECK_AFTER_DAYS = 14


def company_candidates(ctx, known, n=COMPANY_PER_DAY):
    """Layer 2 of the design: websites of companies and research bodies in our base, not yet seen, a few a day.
    No search query is spent; the first stage still sets aside a site that is not about defence."""
    out = []
    for host in sorted(ctx["company"] | ctx["rnd"]):
        site = A.site_of(host)
        if not site or site in known or site in C.AGGREGATORS:
            continue
        known.add(site)
        out.append({"url": f"https://{host}/", "pool_group": "layer2", "prefilter": True,
                    "found_via": {"channel": "base_company", "layer": "layer2",
                                  "country": ctx["company_country"].get(host), "type_hint": "company_news"}})
        if len(out) >= n:
            break
    return out


def recheck_candidates(conn, n=RECHECK_PER_DAY, days=RECHECK_AFTER_DAYS):
    """On-hold sources (stale, too few titles, undated) whose last check is older than `days`: checked again."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT s.candidate_url FROM candidate_seen s WHERE s.last_decision='hold' AND s.decided_at < ? AND "
        "(SELECT d.reasons_json FROM admission_decision d WHERE d.candidate_url=s.candidate_url "
        " ORDER BY d.decision_id DESC LIMIT 1) NOT LIKE '%no_news_site%' ORDER BY s.decided_at LIMIT ?",
        (cutoff, n)).fetchall()
    return [{"url": u, "pool_group": "recheck", "found_via": {"channel": "recheck", "layer": "recheck"}}
            for (u,) in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="read the state and plan; no search, no site, no write")
    a = ap.parse_args()
    tavily = bool(os.environ.get("TAVILY_API_KEY", "").strip())
    C.LIMITS, C.QUOTAS, C.GEMINI_SHARE, C.NEW_CAPS = daily_limits(tavily)
    started = dt.datetime.now(dt.timezone.utc)
    run_id = "gh-" + os.environ.get("GITHUB_RUN_ID", "local") + "-" + started.strftime("%Y%m%dT%H%M")
    with pg_sync.connect() as pg, tempfile.TemporaryDirectory() as tmp:
        conn, ctx = pg_sync.load(pg, Path(tmp) / "registry.sqlite")
        A.set_base(ctx["company"], ctx["rnd"])
        cat = P.load()
        countries = list(cat["countries"])
        jobs = {"layer1": P.layer1_jobs(cat, countries, None, ctx["done"]),
                "layer3": P.layer3_jobs(cat, ctx["events"], 50, ctx["done"]),
                "section": P.section_jobs(C.general_sites(conn)) if tavily else [],
                "types": P.type_jobs(cat, countries, ctx["done"])}
        todo = C.assign_engines(P.plan_cycle(jobs, C.QUOTAS, ctx["done"]), C.GEMINI_SHARE)
        plan = {"planned": dict(Counter(j["layer"] for j in todo)),
                "engines": dict(Counter(j["engine"] for j in todo)), "limits": C.LIMITS,
                "rules_version": A.RULES_VERSION, "categories_version": cat["version"],
                "state_rows": ctx["state_rows"], "events": len(ctx["events"])}
        print(json.dumps({"run": run_id, **plan}))
        if a.dry_run:
            return
        known = C.known_hosts(conn, ctx["known"])
        Budget, HTTP = C.network_parts()
        budget = Budget({"max_requests": C.LIMITS["http"], "max_seconds": 1800, "max_bytes": 200_000_000})
        http = HTTP(budget, enabled=True)
        engines = C.Engines(tavily=PR.tavily_api, gemini=lambda job: PR.gemini_grounded(job["query"]))
        t0 = time.time()
        again = recheck_candidates(conn)
        extra = company_candidates(ctx, known) + again
        cands, found, done, trace, _obs, counts, results = C.execute(
            todo, engines, http, conn, known, use_feedsearch=True, extra=extra,
            recheck={A.registry.canonical_url(c["url"]) for c in again})
        trace.update(http_requests=budget.requests, seconds=round(time.time() - t0),
                     engines_used=dict(engines.used), engines_off=sorted(engines.off))
        measure = C.measure_cycle(cands, found, results, known)
        measure["disputed_total"] = len(A.disputed_batch(conn))
        run = {"run_id": run_id, "started_at": started.isoformat(),
               "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(), "where_run": "github" if
               os.environ.get("GITHUB_RUN_ID") else "local", "plan": plan, "trace": trace,
               "measure": measure, "done_jobs": done}
        written = pg_sync.save(pg, conn, ctx, run)
    # public log: counts only
    print(json.dumps({"trace": {k: trace.get(k) for k in ("queries", "failed", "http_requests", "seconds",
                                                          "engines_used", "engines_off", "stop", "network_recovered")},
                      "decisions": counts, "written": written,
                      "measure": {k: measure.get(k) for k in ("candidates", "n_countries", "n_types", "accepted_share",
                                                              "accepted_share_incl_general", "disputed_for_owner",
                                                              "known_reissued", "saturation", "a1sd_cycle_ok")}}))


if __name__ == "__main__":
    main()
