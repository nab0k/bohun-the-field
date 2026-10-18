"""Run metrics and completeness against benchmarks (EXP-012, AC6).

A benchmark is a list of {"name": ..., "country": ISO2 (optional), "ids": [[scheme, value], ...] (optional)}.
A benchmark entry counts as covered if any of its identifiers is known, or if its exact normalized
name (canonical or alias) exists in the same country. Benchmarks themselves (SIPRI Top 100, top DoD
recipients, etc.) are loaded separately after consent; none is bundled here.
"""
from .normalize import name_norm


def summary(store):
    q = store.query
    return {
        "companies": q("SELECT COUNT(*) n FROM company")[0]["n"],
        "by_status": {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM company GROUP BY status")},
        "by_layer": {str(r["layer"]): r["n"] for r in q("SELECT layer, COUNT(*) n FROM company GROUP BY layer")},
        "by_country": {str(r["country"]): r["n"] for r in q(
            "SELECT country, COUNT(*) n FROM company WHERE status!='excluded' GROUP BY country ORDER BY n DESC")},
        "with_identifier": q("SELECT COUNT(DISTINCT company_id) n FROM company_identifier")[0]["n"],
        "with_category": q("SELECT COUNT(DISTINCT company_id) n FROM company_category")[0]["n"],
        "match_candidates": q("SELECT COUNT(*) n FROM match_candidate")[0]["n"],
        "excluded_reasons": {r["excluded_reason"]: r["n"] for r in q(
            "SELECT excluded_reason, COUNT(*) n FROM company WHERE status='excluded' GROUP BY excluded_reason")},
        "ownership_links": q("SELECT COUNT(*) n FROM company_ownership_link")[0]["n"],
        "observations_by_source": {r["source"]: r["n"] for r in q(
            "SELECT source, COUNT(*) n FROM observation GROUP BY source")},
        "last_runs": q("""SELECT source, status, finished_at, fetched, new_observations, new_companies, matched,
                          candidates, error FROM run ORDER BY id DESC LIMIT 20"""),
    }


def completeness(store, benchmark):
    covered, missing = [], []
    for entry in benchmark:
        hit = None
        for scheme, value in entry.get("ids") or []:
            hit = hit or store.company_by_identifier(scheme, value)
        if not hit:
            ids = store.companies_by_name(name_norm(entry["name"]), entry.get("country"))
            hit = ids[0] if len(ids) == 1 else None
        (covered if hit else missing).append(entry["name"])
    total = len(benchmark)
    return {"total": total, "covered": len(covered), "share": round(len(covered) / total, 3) if total else None,
            "missing": missing}


def sample(store, n):
    """Spread sample for manual review (T08: 20 companies). Public-source fields only.

    Ukrainian companies are counted but not listed: the review list may end up in a public CI log.
    """
    rows = store.query(
        """SELECT id, name, country, layer, status, supply_role, ownership_check FROM company
           WHERE notion_page_id IS NULL AND (country IS NULL OR country != 'UA') ORDER BY id""")
    step = max(1, len(rows) // n) if rows else 1
    picked = rows[::step][:n]
    out = []
    for r in picked:
        cats = store.query("SELECT axis, value, basis FROM company_category WHERE company_id=? ORDER BY axis, value",
                           (r["id"],))
        sig = store.query("SELECT type, layer, counterpart, url FROM signal WHERE company_id=? ORDER BY layer LIMIT 3",
                          (r["id"],))
        ids = store.query("SELECT scheme, value FROM company_identifier WHERE company_id=? ORDER BY scheme",
                          (r["id"],))
        out.append({k: r[k] for k in ("name", "country", "layer", "status", "supply_role", "ownership_check")}
                   | {"identifiers": [f"{i['scheme']}:{i['value']}" for i in ids],
                      "categories": [f"{c['axis']}:{c['value']} ({c['basis']})" for c in cats],
                      "signals": [f"{x['type']}/{x['layer']} {x['counterpart'] or ''} {x['url'] or ''}".strip()
                                  for x in sig]})
    ua = store.query("SELECT COUNT(*) n FROM company WHERE country='UA'")[0]["n"]
    return {"listed": out, "ukraine_not_listed": ua}
