"""USAspending.gov (no key): DoD prime recipients and their subcontractors.

Known source artefacts (checked 2026-10-18): sub-award "amount" can equal the record id, so
amounts are never used; one company can have several UEIs (grouping via the recipient profile parent).

T08 findings 2–3: the recipient list gives neither country nor 4-digit PSC. For each recipient the
adapter reads (a) the recipient profile -> legal country and parent, (b) the PSC breakdown of that
recipient inside the queried group -> up to 3 four-digit PSC codes. Both reads are optional: on
failure the record keeps only the group code (scheme PSC_GROUP, not classified) and no country.
NOTE: the filter key `recipient_id` and the profile fields follow the public API docs and were not
re-checked live after the T08 trial (no runs allowed); the fixture tests cover the parsing only.
"""
from ..model import Mention, Record, SignalIn
from ..normalize import clean_name, country_iso2
from .http import SourceError

API = "https://api.usaspending.gov/api/v2"
SOURCE_RECIPIENTS = "usaspending_recipients"
SOURCE_SUBAWARDS = "usaspending_subawards"

# Defence product/service groups queried one by one, so every recipient keeps the code it came from.
DEFAULT_PSC_GROUPS = ["10", "13", "14", "15", "16", "19", "23", "28", "58", "59", "AC"]


def _filters(fy_start, fy_end, psc=None):
    f = {"time_period": [{"start_date": fy_start, "end_date": fy_end}],
         "agencies": [{"type": "awarding", "tier": "toptier", "name": "Department of Defense"}],
         "award_type_codes": ["A", "B", "C", "D"]}
    if psc:
        # PSC tree filter (checked on the live API 2026-10-18): tier names "Product" / "Research and Development".
        tier = "Research and Development" if psc[:1].isalpha() else "Product"
        f["psc_codes"] = {"require": [[tier, psc]]}
    return f


class Recipients:
    """Cursor format: '<psc_group_index>:<page>'."""
    source = SOURCE_RECIPIENTS

    def __init__(self, fy_start="2024-10-01", fy_end="2025-09-30", psc_groups=None, page_size=100):
        self.fy_start, self.fy_end = fy_start, fy_end
        self.psc_groups = psc_groups or DEFAULT_PSC_GROUPS
        self.page_size = page_size

    def fetch(self, http, cursor, limit):
        gi, page = (int(x) for x in (cursor or "0:1").split(":"))
        records = []
        while len(records) < limit and gi < len(self.psc_groups):
            psc = self.psc_groups[gi]
            body = {"filters": _filters(self.fy_start, self.fy_end, psc), "category": "recipient",
                    "limit": min(self.page_size, limit - len(records)), "page": page}
            data = http.post_json(f"{API}/search/spending_by_category/recipient/", body) or {}
            for item in data.get("results") or []:
                records.append(self.parse(item, psc, self.profile(http, item), self.psc_codes(http, item, psc)))
            if (data.get("page_metadata") or {}).get("hasNext"):
                page += 1
            else:
                gi, page = gi + 1, 1
        return records, f"{gi}:{page}" if gi < len(self.psc_groups) else None

    @staticmethod
    def profile(http, item):
        rid = item.get("recipient_id")
        if not rid:
            return None
        try:
            return http.get_json(f"{API}/recipient/{rid}/")
        except SourceError:
            return None

    def psc_codes(self, http, item, psc):
        rid = item.get("recipient_id")
        if not rid:
            return []
        body = {"filters": dict(_filters(self.fy_start, self.fy_end, psc), recipient_id=rid),
                "category": "psc", "limit": 3, "page": 1}
        try:
            data = http.post_json(f"{API}/search/spending_by_category/psc/", body) or {}
        except SourceError:
            return []
        return [r["code"] for r in data.get("results") or [] if len(str(r.get("code") or "")) == 4][:3]

    @staticmethod
    def parse(item, psc, profile=None, psc_codes=()):
        uei = item.get("uei")
        key = f"{uei or item.get('recipient_id')}:{psc}"
        profile = profile or {}
        loc = profile.get("location") or {}
        country = country_iso2(loc.get("country_code"))
        mentions = []
        parent_rid = profile.get("parent_id")
        parents = []
        if parent_rid and parent_rid != item.get("recipient_id") and profile.get("parent_name"):
            parents = [("USASPENDING_RECIPIENT", parent_rid)]
            mentions.append(Mention(
                name=clean_name(profile["parent_name"]),
                identifiers=([("UEI", profile["parent_uei"])] if profile.get("parent_uei") else []) + parents))
        codes = [("PSC", c) for c in psc_codes] or [("PSC_GROUP", psc)]
        mentions.append(Mention(
            name=clean_name(item.get("name")), country=country,
            identifiers=([("UEI", uei)] if uei else [])
            + ([("USASPENDING_RECIPIENT", item["recipient_id"])] if item.get("recipient_id") else []),
            codes=codes, parents=parents,
            signals=[SignalIn(type="contract", counterpart="US Department of Defense")]))
        payload = dict(item, profile={"country_code": loc.get("country_code"), "parent_id": parent_rid,
                                      "parent_name": profile.get("parent_name")}, psc_codes=list(psc_codes))
        return Record(SOURCE_RECIPIENTS, key, payload, mentions)


class Subawards:
    """Cursor format: page number (string)."""
    source = SOURCE_SUBAWARDS
    FIELDS = ["Sub-Award ID", "Sub-Awardee Name", "Sub-Award Date", "Prime Award ID", "Prime Recipient Name",
              "prime_award_recipient_id", "Sub-Recipient UEI", "Sub-Recipient Location", "NAICS", "PSC"]

    def __init__(self, fy_start="2024-10-01", fy_end="2025-09-30", page_size=100):
        self.fy_start, self.fy_end, self.page_size = fy_start, fy_end, page_size

    def fetch(self, http, cursor, limit):
        page = int(cursor or 1)
        records = []
        while len(records) < limit:
            body = {"filters": _filters(self.fy_start, self.fy_end), "fields": self.FIELDS, "subawards": True,
                    "limit": min(self.page_size, limit - len(records)), "page": page,
                    "sort": "Sub-Award Date", "order": "desc"}
            data = http.post_json(f"{API}/search/spending_by_award/", body) or {}
            for item in data.get("results") or []:
                records.append(self.parse(item))
            page += 1
            if not (data.get("page_metadata") or {}).get("hasNext"):
                return records, None
        return records, str(page)

    @staticmethod
    def parse(item):
        loc = item.get("Sub-Recipient Location") or {}
        country = country_iso2(loc.get("location_country_code"))
        uei = item.get("Sub-Recipient UEI")
        codes = []
        for scheme in ("NAICS", "PSC"):
            code = (item.get(scheme) or {}).get("code")
            if code:
                codes.append((scheme, code))
        mention = Mention(
            name=clean_name(item.get("Sub-Awardee Name")), country=country,
            city=(loc.get("city_name") or None) if country != "UA" else None,
            identifiers=[("UEI", uei)] if uei else [], codes=codes,
            signals=[SignalIn(type="subcontract", counterpart=clean_name(item.get("Prime Recipient Name")),
                              date=item.get("Sub-Award Date"))],
            supplies_to=[("USASPENDING_RECIPIENT", item["prime_award_recipient_id"])]
            if item.get("prime_award_recipient_id") else [])
        return Record(SOURCE_SUBAWARDS, str(item.get("internal_id") or item.get("Sub-Award ID")), item, [mention])
