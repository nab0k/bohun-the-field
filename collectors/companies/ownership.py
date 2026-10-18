"""Identity enrichment via GLEIF and the RU/CN rule (EXP-012, DESIGN v0 §4 stages 3–4).

- Legal country RU or CN, or GLEIF ultimate parent in RU/CN -> status 'excluded' (record kept).
- Partial ownership by RU/CN (from a disclosed source) -> not excluded: company_ownership_link + flag
  OWNERSHIP_LINK_RU / OWNERSHIP_LINK_CN, shown to users (USER 18.10).
- GLEIF reporting exception (parent exists but unknown, e.g. NO_LEI) -> ownership_check 'OPEN:GLEIF_<reason>'.
- Hong Kong / Macau are flagged CN_SAR for review (not excluded automatically).
"""
from .normalize import EXCLUDED_COUNTRIES


def apply_country_rule(store, company_id):
    row = store.get(company_id)
    if row["status"] == "excluded":
        return True
    if row["country"] in EXCLUDED_COUNTRIES:
        store.update(company_id, status="excluded", excluded_reason=f"legal_country_{row['country']}")
        return True
    if row["country"] in ("HK", "MO"):
        store.add_flags(company_id, ["CN_SAR"])
    return False


def record_partial_ownership(store, company_id, owner_country, source, owner_name=None, share_text=None,
                             link_type="minority"):
    """Disclosed partial ownership by RU/CN: visible link, never an exclusion."""
    store.add_ownership_link(company_id, owner_country, link_type, source, owner_name, share_text)
    if owner_country in EXCLUDED_COUNTRIES:
        store.add_flags(company_id, [f"OWNERSHIP_LINK_{owner_country}"])


def enrich_with_gleif(store, gleif, company_id):
    """Attach LEI and ultimate parent. Returns a short outcome string."""
    row = store.get(company_id)
    if row["status"] == "excluded":
        return "excluded"
    if any(s == "LEI" for s, _ in store.identifiers_of(company_id)):
        lei = dict(store.identifiers_of(company_id))["LEI"]
    else:
        if not row["country"]:
            return "no_country"  # without a country an exact GLEIF match is not safe
        hit = gleif.find_exact(row["name"], row["country"])
        if not hit:
            return "no_exact_lei"
        if not store.add_identifier(company_id, "LEI", hit["lei"], "gleif"):
            return "lei_owned_by_other"
        store.add_alias(company_id, hit["legal_name"], "gleif")
        lei = hit["lei"]
    kind, value = gleif.ultimate_parent(lei)
    if kind == "exception":
        store.update(company_id, ownership_check=f"OPEN:GLEIF_{value}")
        return "parent_unknown"
    if kind != "parent" or not value or value["lei"] == lei:
        return "no_parent"
    parent_id = store.company_by_identifier("LEI", value["lei"])
    if not parent_id:
        parent_id = store.create_company(value["legal_name"], country=value["country"])
        store.add_identifier(parent_id, "LEI", value["lei"], "gleif")
    store.add_relation(parent_id, company_id, "parent_of", "gleif")
    record = value["country"] in EXCLUDED_COUNTRIES
    if record:
        store.add_ownership_link(company_id, value["country"], "ultimate_parent", "gleif", value["legal_name"])
        store.update(company_id, status="excluded", excluded_reason=f"ultimate_parent_{value['country']}")
        apply_country_rule(store, parent_id)
        return "excluded_by_parent"
    apply_country_rule(store, parent_id)
    return "parent_linked"
