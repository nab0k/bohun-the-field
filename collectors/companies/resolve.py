"""Resolve a mention to a company and apply it (EXP-012, DESIGN v0 §4 stage 2).

Order: exact identifier -> exact normalized name in the same country (only if unambiguous and no
conflicting identifier of the same scheme) -> new company. Anything unclear goes to match_candidate.
Companies are never merged automatically.
"""
from .normalize import name_norm
from .taxonomy import SIGNAL_LAYER, classify, is_rnd

STRONG_SCHEMES = ("UEI", "LEI", "EDRPOU", "CAGE", "USASPENDING_RECIPIENT")


def _scheme_conflict(store, company_id, identifiers):
    """True if the company already has a different value for one of the mention's identifier schemes."""
    have = {}
    for scheme, value in store.identifiers_of(company_id):
        have.setdefault(scheme, set()).add(value)
    return any(scheme in have and str(value) not in have[scheme] for scheme, value in identifiers
               if scheme in STRONG_SCHEMES or scheme.startswith("NATIONAL:"))


def resolve(store, mention, observation_id):
    """Return (company_id or None, outcome) where outcome in matched|new|candidate."""
    if mention.notion_page_id:
        row = store.query("SELECT id FROM company WHERE notion_page_id=?", (mention.notion_page_id,))
        if row:
            return row[0]["id"], "matched"
        # Every legacy card stays its own record (AC5); same-name cards are flagged for review only.
        for cid in store.companies_by_name(name_norm(mention.name), mention.country):
            store.add_match_candidate(observation_id, cid, "legacy_same_name", mention.name)
        cid = store.create_company(mention.name, country=mention.country, city=mention.city,
                                   website=mention.website, notion_page_id=mention.notion_page_id,
                                   flags=mention.flags, supply_role=mention.supply_role)
        return cid, "new"
    owners = {store.company_by_identifier(s, v) for s, v in mention.identifiers}
    owners.discard(None)
    if len(owners) == 1:
        return owners.pop(), "matched"
    if len(owners) > 1:
        for cid in owners:
            store.add_match_candidate(observation_id, cid, "identifier_conflict", mention.name)
        return None, "candidate"
    norm = name_norm(mention.name)
    if not norm:
        return None, "skipped"
    same = store.companies_by_name(norm, mention.country)
    if len(same) == 1 and not _scheme_conflict(store, same[0], mention.identifiers):
        # A name match alone attaches to an identifier-less lead only if the mention adds no conflict.
        return same[0], "matched"
    if same:
        for cid in same:
            store.add_match_candidate(observation_id, cid, "same_name_needs_review", mention.name)
        # Distinct legal entities with the same name are kept separate: create a new record.
    cid = store.create_company(mention.name, country=mention.country, city=mention.city, website=mention.website,
                               notion_page_id=mention.notion_page_id, flags=mention.flags,
                               supply_role=mention.supply_role)
    return cid, "new"


def apply(store, company_id, mention, observation_id, source):
    store.fill_missing(company_id, country=mention.country, city=mention.city, website=mention.website)
    if mention.flags:
        store.add_flags(company_id, mention.flags)
    store.add_alias(company_id, mention.name, source)
    for scheme, value in mention.identifiers:
        store.add_identifier(company_id, scheme, value, source)
    for s in mention.signals:
        layer = s.layer or SIGNAL_LAYER.get(s.type)
        if layer:
            store.add_signal(company_id, s.type, layer, observation_id, s.counterpart, s.date, s.url)
    rnd = False
    for scheme, code in mention.codes:
        for axis, value, basis in classify(scheme, code):
            store.add_category(company_id, axis, value, basis)
        rnd = rnd or is_rnd(scheme, code)
    for axis, value, basis in mention.legacy_categories:
        store.add_category(company_id, axis, value, basis)
    if rnd:
        store.fill_missing(company_id, supply_role="RND")
    for scheme, value in mention.parents:
        parent = store.company_by_identifier(scheme, value)
        if parent:
            store.add_relation(parent, company_id, "parent_of", source)
    for scheme, value in mention.supplies_to:
        buyer = store.company_by_identifier(scheme, value)
        if buyer:
            store.add_relation(company_id, buyer, "supplies", source)
        else:
            store.add_flags(company_id, ["SUPPLIES_TO_UNRESOLVED"])
