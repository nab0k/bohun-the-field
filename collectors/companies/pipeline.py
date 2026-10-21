"""One collection run per source (EXP-012, DESIGN v0 §4, §6).

fetch (bounded, from cursor) -> observation (idempotent) -> resolve/apply mentions -> RU/CN rule
-> layer recompute. A failing source does not stop others; its cursor does not move; after
MAX_FAILURES consecutive failures it is disabled until a manual decision (AC7).
"""
import traceback

from . import ownership, resolve
from .store import now
from .normalize import name_norm
from .taxonomy import best_layer

MAX_FAILURES = 3


# Signals kept for review only: they never set the company layer (a legacy card imports as a lead, AC5).
INFO_ONLY_SIGNALS = ("legacy_assessment",)


def recompute_layer(store, company_id):
    rows = store.query("SELECT type, layer FROM signal WHERE company_id=?", (company_id,))
    layers = [r["layer"] for r in rows if r["type"] not in INFO_ONLY_SIGNALS]
    store.update(company_id, layer=best_layer(layers))


def run_source(store, adapter, http, limit):
    state = store.source_state(adapter.source)
    stats = dict(source=adapter.source, started_at=now(), cursor_in=state["cursor"], fetched=0,
                 new_observations=0, new_companies=0, matched=0, candidates=0, skipped=0,
                 discoveries=0, candidate_existing=0, holds=0)
    if state["disabled"]:
        store.save_run(**stats, finished_at=now(), cursor_out=state["cursor"], status="skipped_disabled")
        store.commit()
        return stats | {"status": "skipped_disabled"}
    try:
        records, cursor_out = adapter.fetch(http, state["cursor"], limit)
        touched = set()
        for rec in records:
            stats["fetched"] += 1
            obs_id, is_new = store.add_observation(rec.source, rec.source_key, rec.payload)
            if not is_new:
                continue  # same content already processed (AC1)
            stats["new_observations"] += 1
            for discovery in rec.discoveries:
                norm = name_norm(discovery.name)
                matches = store.companies_by_discovery_name(norm) if norm else []
                if not norm:
                    status, reason, matched_id = "hold", "unusable_normalized_name", None
                elif not matches:
                    status, reason, matched_id = "discovered", None, None
                elif len(matches) == 1:
                    status, reason, matched_id = "candidate_existing", "exact_normalized_name_only", matches[0]
                else:
                    status, reason, matched_id = "hold", "multiple_exact_normalized_name_matches", None
                    for cid in matches:
                        store.add_match_candidate(obs_id, cid, reason, discovery.name)
                store.add_discovery_candidate(obs_id, discovery, status, matched_id, reason)
                stats[{"discovered": "discoveries", "candidate_existing": "candidate_existing",
                       "hold": "holds"}[status]] += 1
            for mention in rec.mentions:
                cid, outcome = resolve.resolve(store, mention, obs_id)
                key = {"matched": "matched", "new": "new_companies", "candidate": "candidates"}.get(outcome, "skipped")
                stats[key] += 1
                if cid:
                    resolve.apply(store, cid, mention, obs_id, rec.source)
                    touched.add(cid)
        for cid in touched:
            ownership.apply_country_rule(store, cid)
            recompute_layer(store, cid)
        store.save_source_state(adapter.source, cursor_out, 0, False)
        store.save_run(**stats, finished_at=now(), cursor_out=cursor_out, status="ok")
        store.commit()
        return stats | {"status": "ok", "cursor_out": cursor_out}
    except Exception as err:  # noqa: BLE001 — isolate one source from the others
        store.rollback()
        failures = state["consecutive_failures"] + 1
        store.save_source_state(adapter.source, state["cursor"], failures, failures >= MAX_FAILURES)
        store.save_run(**stats, finished_at=now(), cursor_out=state["cursor"], status="error",
                       error=f"{type(err).__name__}: {err}"[:500])
        store.commit()
        return stats | {"status": "error", "error": str(err), "trace": traceback.format_exc(limit=3)}


def enrich_identities(store, gleif, limit):
    """Bounded GLEIF pass over companies without LEI and without an ownership decision."""
    rows = store.query(
        """SELECT c.id FROM company c WHERE c.status != 'excluded' AND c.country IS NOT NULL
           AND c.ownership_check IS NULL
           AND NOT EXISTS (SELECT 1 FROM company_identifier i WHERE i.company_id=c.id AND i.scheme='LEI')
           ORDER BY c.created_at LIMIT ?""", (limit,))
    outcomes = {}
    for r in rows:
        try:
            out = ownership.enrich_with_gleif(store, gleif, r["id"])
        except Exception as err:  # noqa: BLE001
            out = f"error:{type(err).__name__}"
        if out in ("no_exact_lei", "no_country", "lei_owned_by_other"):
            store.update(r["id"], ownership_check=f"GLEIF_{out.upper()}")
        recompute_layer(store, r["id"])
        outcomes[out] = outcomes.get(out, 0) + 1
    store.commit()
    return outcomes
