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


GROUP_WORDS = {"group", "gruppen", "groupe", "gruppe", "holding", "holdings"}


def _group_core(name):
    """'Dassault Aviation Group' -> 'dassault aviation'; benchmark names are groups, sources name entities."""
    words = name_norm(name).split()
    while words and words[-1] in GROUP_WORDS:
        words.pop()
    return " ".join(words)


def _find_group(store, entry):
    """Any company whose normalized name starts with the group core as whole words (same country if known).

    Cores shorter than 4 characters (HII, KBR, CAE, NEC) are too ambiguous and only match exactly.
    """
    core = _group_core(entry["name"])
    if len(core) < 4:
        return None
    n = len(core)
    country_sql, params = "", [core, n, n + 1, core, n, n + 1]
    if entry.get("country"):
        country_sql, params = " AND c.country = ?", params + [entry["country"]]
    rows = store.query(f"""SELECT DISTINCT c.id FROM company c LEFT JOIN company_alias a ON a.company_id = c.id
        WHERE ((substr(c.name_norm, 1, {n}) = ? AND (length(c.name_norm) = ? OR substr(c.name_norm, ?, 1) = ' '))
            OR (substr(a.alias_norm, 1, {n}) = ? AND (length(a.alias_norm) = ? OR substr(a.alias_norm, ?, 1) = ' ')))
        {country_sql}""", tuple(params))
    return rows[0]["id"] if rows else None


def _find(store, entry):
    for scheme, value in entry.get("ids") or []:
        hit = store.company_by_identifier(scheme, value)
        if hit:
            return hit
    ids = store.companies_by_name(name_norm(entry["name"]), entry.get("country"))
    if not ids and not entry.get("country"):
        # trans-national entries (SIPRI "Trans-European"): accept a unique exact name in any country
        rows = store.query("""SELECT DISTINCT c.id FROM company c LEFT JOIN company_alias a ON a.company_id = c.id
                              WHERE c.name_norm = ? OR a.alias_norm = ?""", (name_norm(entry["name"]),) * 2)
        ids = [r["id"] for r in rows]
    return ids[0] if len(ids) == 1 else None


def completeness(store, benchmark):
    """Share of benchmark entries the store knows.

    Covered = found by identifier or by unique exact name (+ country). A group counts as covered when the
    found company is a parent with known subsidiaries too. Entries marked `expected: excluded_ru_cn` are a
    separate check: they must be absent or excluded, never `card`.
    """
    covered, missing, ru_cn = [], [], {"absent": 0, "excluded": 0, "not_excluded": []}
    by_group = []
    for entry in benchmark:
        hit = _find(store, entry)
        if not hit and entry.get("expected") != "excluded_ru_cn":
            hit = _find_group(store, entry)
            if hit:
                by_group.append(entry["name"])
        if entry.get("expected") == "excluded_ru_cn":
            if not hit:
                ru_cn["absent"] += 1
            elif store.get(hit)["status"] == "excluded":
                ru_cn["excluded"] += 1
            else:
                ru_cn["not_excluded"].append(entry["name"])
            continue
        (covered if hit else missing).append(entry["name"])
    total = len(covered) + len(missing)
    return {"total": total, "covered": len(covered), "share": round(len(covered) / total, 3) if total else None,
            "missing": missing, "covered_by_group_name": by_group, "ru_cn_check": ru_cn}


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
