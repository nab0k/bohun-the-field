"""A1SD-03 self-improvement (D-20261119-A1SD-SELF-IMPROVE-G2): every run learns numbers, never code.

What a run keeps (table source_discovery.learning, one row per asked job): layer, engine, country, type hint, how many
results and new sites it gave and what became of them. What the next run does with it:
- layer quotas follow the yield (accepted per query over 7 days), every search layer keeps at least 10% for scouting,
  the free limits are never exceeded (the total stays the default total);
- countries and types with the fewest accepted sources go first inside a layer;
- a cell (layer x country) whose last 3 jobs gave no new site sleeps 30 days (its jobs go to the end);
- if the last run's search share fell below half of the 7-day mean, the defaults come back ("reset").
Code changes are only proposed (weekly report -> PR by the owner's word); nothing here edits files.
"""
import datetime as dt
from collections import Counter, defaultdict

SEARCH_LAYERS = ("types", "layer1", "layer3", "section")
FLOOR = 0.10
WINDOW_DAYS = 7
SLEEP_AFTER = 3
SLEEP_DAYS = 30
MIN_QUERIES = 20      # below this many asked search jobs in the window the defaults stay (too little to learn from)


def rows_for_run(run_id, job_stats, results):
    """job_stats: {job_key: {layer, engine, country, type_hint, results, new}}; results: admission results with
    found_via_job. One learning row per asked job."""
    by_job = defaultdict(Counter)
    for r in results:
        if r.get("found_via_job"):
            by_job[r["found_via_job"]][r["decision"]] += 1
    out = []
    for key, s in job_stats.items():
        d = by_job.get(key, Counter())
        out.append({"run_id": run_id, "job_key": key, "layer": s["layer"], "engine": s.get("engine"),
                    "country": s.get("country"), "type_hint": s.get("type_hint"), "results": s.get("results", 0),
                    "new_sites": s.get("new", 0), "accepted": d["accept"], "disputed": d["disputed"],
                    "general": d["general"], "held": d["hold"], "rejected": d["reject"]})
    return out


def _recent(history, now, days):
    cut = now - dt.timedelta(days=days)
    return [h for h in history if h["at"] >= cut]


def adapt(quotas, gemini, history, recent_runs, now=None, useful=None):
    """New layer quotas from the yield. history: learning rows with "at" (datetime); recent_runs: [(at, share)] newest
    first; useful (A6SH-02, 20.11): {layer: (sources with 5+ articles, of them reaching events)} - a layer whose sources
    reach events weighs more (x0.5..x1.5), so the search learns usefulness, not just admission. Returns (quotas,
    gemini, info)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    quotas, gemini = dict(quotas), dict(gemini)
    shares = [s for _, s in recent_runs if s is not None]
    if len(shares) >= 3 and shares[0] < 0.5 * (sum(shares[1:8]) / len(shares[1:8])):
        return quotas, gemini, {"mode": "reset", "last_share": shares[0]}
    window = [h for h in _recent(history, now, WINDOW_DAYS) if h["layer"] in SEARCH_LAYERS]
    layers = [l for l in SEARCH_LAYERS if quotas.get(l)]
    if len(window) < MIN_QUERIES or len(layers) < 2:
        return quotas, gemini, {"mode": "default", "window_jobs": len(window)}
    asked, accepted = Counter(h["layer"] for h in window), Counter()
    for h in window:
        accepted[h["layer"]] += h["accepted"]
    yield_ = {l: (accepted[l] + 1) / (asked[l] + 2) for l in layers}   # smoothed: an unasked layer is not zero
    weight_by_use = {}
    for l in layers:
        n, good = (useful or {}).get(l, (0, 0))
        weight_by_use[l] = 0.5 + good / n if n >= 5 else 1.0
        yield_[l] *= weight_by_use[l]
    total = sum(quotas[l] for l in layers)
    floor = max(1, round(FLOOR * total))
    spare = total - floor * len(layers)
    weight = sum(yield_.values())
    new = {l: floor + int(spare * yield_[l] / weight) for l in layers}
    best = max(layers, key=lambda l: yield_[l])
    new[best] += total - sum(new.values())                           # rounding remainder to the best layer
    for l in layers:
        g = gemini.get(l, 0)
        gemini[l] = min(new[l], round(g * new[l] / quotas[l])) if quotas[l] else 0
        quotas[l] = new[l]
    return quotas, gemini, {"mode": "learned", "window_jobs": len(window),
                            "yield": {l: round(v, 3) for l, v in yield_.items()},
                            "usefulness_weight": {l: round(v, 2) for l, v in weight_by_use.items() if v != 1.0}}


def sleeping_cells(history, now=None):
    """(layer, country) whose last SLEEP_AFTER jobs within SLEEP_DAYS gave no new site."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cells = defaultdict(list)
    for h in sorted(_recent(history, now, SLEEP_DAYS), key=lambda h: h["at"], reverse=True):
        cells[(h["layer"], h.get("country"))].append(h["new_sites"])
    return {c for c, news in cells.items() if len(news) >= SLEEP_AFTER and not any(news[:SLEEP_AFTER])}


def order_by_gaps(jobs, accepted_by_country, accepted_by_type, asleep):
    """Stable reorder inside a layer: awake cells first, then the countries and types with the fewest accepted
    sources. The layer's own priority (gap categories, events) stays inside equal keys."""
    return sorted(jobs, key=lambda j: ((j["layer"], j.get("country")) in asleep,
                                       accepted_by_country.get(j.get("country"), 0),
                                       accepted_by_type.get(j.get("type_hint"), 0)))


def streak(recent_ok, current_ok):
    """Successful runs in a row, the current one included. recent_ok: a1sd_cycle_ok of earlier runs, newest first."""
    if not current_ok:
        return 0
    n = 1
    for ok in recent_ok:
        if not ok:
            break
        n += 1
    return n
