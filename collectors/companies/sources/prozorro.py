"""Prozorro (Ukraine) public API 2.5 (no key): winners of defence-related procurement.

Feed gives tender ids; details give awards with suppliers (UA-EDR = ЄДРПОУ). Only active awards are
used. Defence relevance: CPV (ДК021) in a defence sub-family or procuring entity kind "defense".
Ukraine rule: supplier addresses are never stored (only the country).
Layer (T08 finding 1): A only if the buyer is a defence entity (kind "defense" or a known defence
name); other buyers of defence CPV items give C with the flag NON_DEFENCE_BUYER.
Note: much of wartime defence procurement is not published in Prozorro at all.
"""
from ..model import Mention, Record, SignalIn
from ..normalize import clean_name, country_iso2, is_defence_buyer
from ..taxonomy import is_defence_cpv

API = "https://public-api.prozorro.gov.ua/api/2.5"
SOURCE = "prozorro_awards"


class Awards:
    source = SOURCE

    def __init__(self, page_size=100, max_details=None):
        self.page_size = page_size
        self.max_details = max_details  # bound on detail requests per run (defaults to limit*5)

    def fetch(self, http, cursor, limit):
        """Cursor = feed offset. Walks the feed newest-first, fetching details within a bounded budget."""
        budget = self.max_details or limit * 5
        records, offset, details = [], cursor, 0
        while len(records) < limit and details < budget:
            url = f"{API}/tenders?limit={self.page_size}&descending=1" + (f"&offset={offset}" if offset else "")
            feed = http.get_json(url) or {}
            items = feed.get("data") or []
            if not items:
                return records, None
            for item in items:
                if details >= budget or len(records) >= limit:
                    break
                details += 1
                tender = (http.get_json(f"{API}/tenders/{item['id']}") or {}).get("data")
                if tender:
                    rec = self.parse(tender)
                    if rec:
                        records.append(rec)
            offset = (feed.get("next_page") or {}).get("offset")
            if not offset:
                return records, None
        return records, offset

    @staticmethod
    def parse(tender):
        cpvs = sorted({(i.get("classification") or {}).get("id", "").split("-")[0]
                       for i in tender.get("items") or [] if i.get("classification")})
        buyer = tender.get("procuringEntity") or {}
        defence_buyer = buyer.get("kind") == "defense" or is_defence_buyer(buyer.get("name"))
        defence = any(is_defence_cpv(c) for c in cpvs) or defence_buyer
        if not defence:
            return None
        url = f"https://prozorro.gov.ua/tender/{tender.get('tenderID')}" if tender.get("tenderID") else None
        codes = [("CPV", c) for c in cpvs if is_defence_cpv(c)]
        mentions = []
        for award in tender.get("awards") or []:
            if award.get("status") != "active":
                continue
            for s in award.get("suppliers") or []:
                ident = s.get("identifier") or {}
                country = country_iso2((s.get("address") or {}).get("countryName")) or "UA"
                identifiers = [("EDRPOU", ident["id"])] if ident.get("scheme") == "UA-EDR" and ident.get("id") else []
                mentions.append(Mention(
                    name=clean_name(ident.get("legalName") or s.get("name")), country=country,
                    identifiers=identifiers, codes=codes,
                    flags=[] if defence_buyer else ["NON_DEFENCE_BUYER"],
                    signals=[SignalIn(type="tender_award", counterpart=clean_name(buyer.get("name")),
                                      date=(award.get("date") or "")[:10] or None, url=url,
                                      layer="A" if defence_buyer else "C")]))
        if not mentions:
            return None
        # Payload kept without supplier street addresses (Ukraine rule).
        slim = {"id": tender.get("id"), "tenderID": tender.get("tenderID"), "dateModified": tender.get("dateModified"),
                "cpv": cpvs, "buyer": {"name": buyer.get("name"), "kind": buyer.get("kind"),
                                       "edrpou": (buyer.get("identifier") or {}).get("id")},
                "suppliers": [{"name": m.name, "edrpou": dict(m.identifiers).get("EDRPOU")} for m in mentions]}
        return Record(SOURCE, tender.get("id"), slim, mentions)
