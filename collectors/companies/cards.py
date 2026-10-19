"""Promotion rule lead -> public card (EXP-012, decision D02).

A company becomes `card` only if it has
1. a legal binding: an identifier from a legal-entity register or an official supplier registry
   (REGISTRY_SCHEMES), and
2. at least one defence or dual-use activity source: a signal of layer A or B (legacy assessments
   do not count, see pipeline.INFO_ONLY_SIGNALS), and
3. it is not excluded and not a public body (ORG_TYPE:PUB is an authority, not a company).
A card that no longer meets the rule goes back to `candidate`. `dossier` and `excluded` are never touched.
"""
import json

from .pipeline import INFO_ONLY_SIGNALS

REGISTRY_SCHEMES = ("LEI", "UEI", "CAGE", "EDRPOU", "JP_CORP", "EU_PIC")
CARD_LAYERS = ("A", "B")


def is_registry(scheme):
    return scheme in REGISTRY_SCHEMES or scheme.startswith("NATIONAL:")


def eligible_ids(store):
    """Ids meeting the rule, computed in SQL (works on SQLite and PostgreSQL; no LIKE/% in queries)."""
    schemes = ", ".join(f"'{x}'" for x in REGISTRY_SCHEMES)
    info = ", ".join(f"'{x}'" for x in INFO_ONLY_SIGNALS)
    layers = ", ".join(f"'{x}'" for x in CARD_LAYERS)
    rows = store.query(f"""
        SELECT c.id, c.flags FROM company c
        WHERE c.status IN ('candidate', 'card')
          AND EXISTS (SELECT 1 FROM company_identifier i WHERE i.company_id = c.id
                      AND (i.scheme IN ({schemes}) OR substr(i.scheme, 1, 9) = 'NATIONAL:'))
          AND EXISTS (SELECT 1 FROM signal s WHERE s.company_id = c.id
                      AND s.layer IN ({layers}) AND s.type NOT IN ({info}))""")
    keep = set()
    for r in rows:
        flags = json.loads(r["flags"]) if isinstance(r["flags"], str) else (r["flags"] or [])
        if "ORG_TYPE:PUB" not in flags:
            keep.add(r["id"])
    return keep


def promote(store):
    """Apply the rule to every candidate/card. Returns counts of changes."""
    ok = eligible_ids(store)
    out = {"promoted": 0, "demoted": 0, "cards": len(ok)}
    for row in store.query("SELECT id, status FROM company WHERE status IN ('candidate', 'card')"):
        if row["id"] in ok and row["status"] == "candidate":
            store.update(row["id"], status="card")
            out["promoted"] += 1
        elif row["id"] not in ok and row["status"] == "card":
            store.update(row["id"], status="candidate")
            out["demoted"] += 1
    store.commit()
    return out
