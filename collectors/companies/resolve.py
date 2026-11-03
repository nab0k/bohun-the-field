"""Resolve a mention to a company and apply it (EXP-012, DESIGN v0 §4 stage 2).

Order: exact identifier -> exact normalized name in the same country (only if unambiguous and no
conflicting identifier of the same scheme) -> new company. Anything unclear goes to match_candidate.
Companies are never merged automatically.
"""
from .normalize import name_norm
from .taxonomy import SIGNAL_LAYER, classify, is_rnd

STRONG_SCHEMES = ("UEI", "LEI", "EDRPOU", "CAGE", "USASPENDING_RECIPIENT", "JP_CORP", "EU_PIC")


def _scheme_conflict(store, company_id, identifiers):
    """True if the company already has a different value for one of the mention's identifier schemes."""
    have = {}
    for scheme, value in store.identifiers_of(company_id):
        have.setdefault(scheme, set()).add(value)
    return any(scheme in have and str(value) not in have[scheme] for scheme, value in identifiers
               if scheme in STRONG_SCHEMES or scheme.startswith("NATIONAL:"))


def resolve(store, mention, observation_id, *, policy="legacy", evidence=None):
    """Return (company_id or None, outcome) where outcome in matched|new|candidate."""
    if policy == "continuous_v0":
        return _continuous_resolve(store, mention, observation_id, evidence)
    if policy != "legacy":
        raise ValueError("UNKNOWN_IDENTITY_POLICY")
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


def _continuous_resolve(store, mention, observation_id, evidence):
    """Strict discovery/linking. Evidence is an admitted source receipt, never an LLM score.

    No call to legacy apply(): returned identity does not grant permission to add
    identifiers/categories/fields. The future transactional writer revalidates it.
    """
    from .automation.contracts import IdentityEvidence, ContractError, https_host
    from .normalize import clean_name
    import re
    import unicodedata

    def surface(value):
        return " ".join(unicodedata.normalize("NFKC", clean_name(value)).casefold().split())

    candidates = set(store.companies_by_name(name_norm(mention.name), mention.country))
    ids = tuple((s, str(v)) for s, v in mention.identifiers)
    owners = {store.company_by_identifier(s, v) for s, v in ids}
    owners.discard(None)
    candidates.update(owners)

    def hold(reason):
        for cid in sorted(candidates):
            store.add_match_candidate(observation_id, cid, reason, mention.name)
        return None, "candidate"

    if not isinstance(evidence, IdentityEvidence):
        return hold("identity_receipt_required")
    if evidence.entity_kind != "company" or not evidence.industry_relevant:
        return hold("type_or_relevance_unconfirmed")
    if evidence.expected_role != evidence.observed_role or evidence.observed_role not in (
            "company", "manufacturer", "supplier", "subcontractor", "grantee"):
        return hold("participant_role_unconfirmed")
    if (surface(evidence.observed_name) != surface(mention.name)
            or evidence.observed_country != mention.country
            or not re.fullmatch(r"[A-Z]{2}", mention.country or "")):
        return hold("source_subject_mismatch")
    if mention.country in ("RU", "CN"):
        return hold("excluded_country")
    for scheme, _ in ids:
        if scheme.startswith("NATIONAL:") and scheme != "NATIONAL:" + mention.country:
            return hold("identifier_jurisdiction_conflict")
        if (scheme in STRONG_SCHEMES or scheme.startswith("NATIONAL:")) and len({v for s, v in ids if s == scheme}) > 1:
            return hold("multiple_identifier_values_need_review")
    if mention.notion_page_id:
        bound = store.query("SELECT id FROM company WHERE notion_page_id=?", (mention.notion_page_id,))
        if bound:
            candidates.add(bound[0]["id"])
            if owners and bound[0]["id"] not in owners:
                return hold("binding_identifier_conflict")
    if len(owners) > 1:
        return hold("identifier_conflict")

    # All identifiers must be consistent, even if one of them matched first.
    for cid in owners:
        have = {}
        for scheme, value in store.identifiers_of(cid):
            have.setdefault(scheme, set()).add(str(value))
        if any(s in have and v not in have[s] for s, v in ids):
            return hold("identifier_conflict")

    admitted_schemes = {s for s in evidence.legal_entity_schemes
                        if s in STRONG_SCHEMES or re.fullmatch(r"NATIONAL:[A-Z]{2}", s)}
    documented = set(evidence.document_identifiers)
    legal_ids = [(s, v) for s, v in ids if s in admitted_schemes and (s, v) in documented]
    legal_owners = {store.company_by_identifier(s, v) for s, v in legal_ids}
    legal_owners.discard(None)

    def usable(cid):
        row = store.get(cid)
        return row and row["status"] != "excluded" and row["country"] == mention.country

    if len(legal_owners) == 1:
        cid = next(iter(legal_owners))
        return (cid, "matched") if usable(cid) else hold("record_protected_or_country_conflict")

    if mention.notion_page_id:
        rows = store.query("SELECT id FROM company WHERE notion_page_id=?", (mention.notion_page_id,))
        if rows:
            cid = rows[0]["id"]
            candidates.add(cid)
            if owners and cid not in owners:
                return hold("binding_identifier_conflict")
            if (evidence.source_kind == "saved_binding" and evidence.bound_page_id == mention.notion_page_id
                    and usable(cid)):
                if _scheme_conflict(store, cid, ids):
                    return hold("identifier_conflict")
                return cid, "matched"
        return hold("notion_binding_needs_review")

    # ADR-018 official-domain route confirms a link, not a corporate merge.
    try:
        host = https_host(evidence.source_url)
        domain_rows = []
        for row in store.query("SELECT id, website FROM company WHERE website IS NOT NULL"):
            try:
                if https_host(row["website"]) == host:
                    domain_rows.append(row["id"])
            except ContractError:
                continue
        candidates.update(domain_rows)
        exact = []
        for cid in candidates:
            row = store.get(cid)
            aliases = store.query("SELECT alias FROM company_alias WHERE company_id=?", (cid,))
            names = [row["name"]] + [r["alias"] for r in aliases]
            if cid in domain_rows and usable(cid) and surface(mention.name) in {surface(n) for n in names}:
                exact.append(cid)
        if (evidence.source_kind == "official_company" and len(exact) == 1 and len(domain_rows) == 1
                and len(candidates) == 1 and not owners):
            cid = exact[0]
            if _scheme_conflict(store, cid, ids):
                return hold("identifier_conflict")
            return cid, "matched"
    except ContractError:
        return hold("official_domain_invalid")

    if candidates:
        return hold("identity_collision")
    if not legal_ids or evidence.source_kind not in ("official_registry", "official_award", "official_company"):
        return hold("legal_identity_not_established")
    if mention.country in ("RU", "CN"):
        return hold("excluded_country")
    # Creating a lead is permitted, but applying its claims/identifiers is separate.
    cid = store.create_company(mention.name, country=mention.country)
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
