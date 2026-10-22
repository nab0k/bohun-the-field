"""GLEIF LEI API (no key): legal identity and accounting-consolidation parents.

Rules (checked 2026-10-18):
- only an exact normalized legal-name match in the same country is accepted (fuzzy search returned
  "SSAB AB" first for "Saab AB" — never used);
- legal forms are equivalent ("Saab AB" == "SAAB Aktiebolag") via normalize.name_norm;
- an ultimate-parent *reporting exception* (e.g. NO_LEI for Nexperia B.V.) means the owner is NOT known
  from GLEIF — recorded as ownership_check OPEN, never as "no foreign owner".
GLEIF has no minority stakes: partial ownership must come from other disclosed sources.
"""
import urllib.parse

from ..normalize import country_iso2, name_norm

API = "https://api.gleif.org/api/v1"
ACCEPT = "application/vnd.api+json"


def _entity(rec):
    if not rec:
        return None
    a = rec.get("attributes") or {}
    e = a.get("entity") or {}
    return {"lei": rec.get("id"), "legal_name": (e.get("legalName") or {}).get("name"),
            "country": country_iso2((e.get("legalAddress") or {}).get("country") or e.get("jurisdiction")),
            "status": e.get("status"), "registered_at": (e.get("registeredAt") or {}).get("id"),
            "registered_as": e.get("registeredAs")}


class Gleif:
    source = "gleif"

    def __init__(self, http):
        self.http = http

    def find_exact(self, name, country):
        """Return the single LEI entity whose normalized legal name and country match, else None."""
        q = urllib.parse.quote(name)
        data = self.http.get_json(f"{API}/lei-records?filter[entity.legalName]={q}&page[size]=10", accept=ACCEPT) or {}
        target = name_norm(name)
        hits = [_entity(r) for r in data.get("data") or []]
        hits = [h for h in hits if h and name_norm(h["legal_name"]) == target and (not country or h["country"] == country)]
        return hits[0] if len(hits) == 1 else None

    def ultimate_parent(self, lei):
        """Return ('parent', entity) | ('exception', reason) | ('none', None)."""
        data = self.http.get_json(f"{API}/lei-records/{lei}/ultimate-parent", accept=ACCEPT)
        if data and data.get("data"):
            return "parent", _entity(data["data"])
        exc = self.http.get_json(f"{API}/lei-records/{lei}/ultimate-parent-reporting-exception", accept=ACCEPT)
        if exc and exc.get("data"):
            return "exception", (exc["data"].get("attributes") or {}).get("reason")
        return "none", None


    def find_exact_candidates(self, name, max_pages=4, page_size=100):
        """All exact normalized-name registry leads from a BOUNDED paginated search.

        Returns (leads, complete). A limited/incomplete search must never be
        interpreted as uniquely identifying a legal entity.
        """
        if not (1 <= max_pages <= 4 and 1 <= page_size <= 100):
            raise ValueError("registry pagination exceeds research bounds")
        target = name_norm(name)
        if not target:
            return [], True
        leads = {}
        scanned = 0
        for page in range(1, max_pages + 1):
            url = (f"{API}/lei-records?filter[entity.legalName]={urllib.parse.quote(name, safe='')}"
                   f"&page[size]={page_size}&page[number]={page}")
            response = self.http.get_json(url, accept=ACCEPT)
            if not isinstance(response, dict) or not isinstance(response.get("data"), list):
                return list(leads.values()), False
            batch = response["data"]
            scanned += len(batch)
            for raw in batch:
                entity = _entity(raw)
                if (entity and entity.get("lei") and
                        name_norm(entity.get("legal_name")) == target):
                    leads[entity["lei"]] = entity
            pagination = (response.get("meta") or {}).get("pagination") or {}
            total = pagination.get("total")
            links = response.get("links") or {}
            next_page = bool(links.get("next"))
            if isinstance(total, int) and scanned < total:
                next_page = True
            if not next_page and len(batch) < page_size:
                return list(leads.values()), True
            if not next_page and isinstance(total, int) and scanned >= total:
                return list(leads.values()), True
            # Exactly a full page without trustworthy totals may have more results;
            # query another page rather than declare this a complete unique match.
            if not batch:
                return list(leads.values()), True
        return list(leads.values()), False
