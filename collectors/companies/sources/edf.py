"""European Defence Fund (EDF) grants: participants of funded projects (no key).

Source: EU Funding & Tenders Search API, index of projects (apiKey SEDIA_NONH2020_PROD, public).
Checked 2026-10-19 with two read requests: `programAbbreviation=EDF` gives 297 projects (EDF 2021-2025,
ASAP, EDIRPA); every project is indexed once per language, so only `languages=["en"]` is requested and
projects are de-duplicated by projectId. `metadata.participants` is a JSON string with, per participant:
role (coordinator/participant), pic (EU participant code), legalName, country ISO2, city, type
(PRC private company, REC research centre, HES university, PUB public body, OTH), eucontribution,
webLink, sme. License: CC BY 4.0 (EDF results, European Commission).
Stored: name, country, city (not for UA), website, PIC. Never stored: street, postal code, coordinates.
"""
import json

from ..model import Mention, Record, SignalIn
from ..normalize import clean_name, country_iso2

API = "https://api.tech.ec.europa.eu/search-api/prod/rest/search"
API_KEY = "SEDIA_NONH2020_PROD"
SOURCE = "edf_grants"
BUYER = "European Commission, European Defence Fund (EDF)"
QUERY = {"bool": {"must": [{"terms": {"programAbbreviation": ["EDF"]}}]}}
RESEARCH_TYPES = {"REC", "HES"}


def _first(md, key):
    value = md.get(key)
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _website(link):
    link = (link or "").strip()
    if not link or link == ".":
        return None
    return link if link.startswith(("http://", "https://")) else "https://" + link


class Grants:
    source = SOURCE

    def __init__(self, page_size=10):
        self.page_size = page_size

    def fetch(self, http, cursor, limit):
        """Cursor 'o:<offset>' = number of index entries already read. `limit` counts projects.

        The page size never exceeds `limit`, so a run reads at most `limit` projects (T08 MVP run 1 read a whole
        page of 50 for limit 10). A legacy cursor '<page>' (page size 50, run 1 on 2026-10-19) is converted.
        """
        if cursor and not str(cursor).startswith("o:"):
            offset = (int(cursor) - 1) * 50
        else:
            offset = int(str(cursor or "o:0")[2:])
        size = max(1, min(self.page_size, limit))
        while offset % size:
            size -= 1  # page boundaries must line up with the offset
        records, seen = [], set()
        while len(records) < limit:
            url = f"{API}?apiKey={API_KEY}&text=***&pageSize={size}&pageNumber={offset // size + 1}"
            data = http.post_form(url, {"query": QUERY, "languages": ["en"]}) or {}
            results = data.get("results") or []
            for item in results:
                rec = self.parse(item.get("metadata") or {})
                if rec and rec.source_key not in seen:
                    seen.add(rec.source_key)
                    records.append(rec)
            offset += len(results)
            total = data.get("totalResults") or 0
            if not results or offset >= total:
                return records, None
        return records, f"o:{offset}"

    @staticmethod
    def parse(md):
        if (_first(md, "programAbbreviation") or "") != "EDF":
            return None
        if (_first(md, "language") or "en") != "en":
            return None  # translations of the same project
        project_id = _first(md, "projectId")
        try:
            people = json.loads(_first(md, "participants") or "[]")
        except ValueError:
            people = []
        if not project_id or not people:
            return None
        call = _first(md, "callIdentifier")
        date = ((_first(md, "ecSignatureDate") or _first(md, "startDate")) or "")[:10] or None
        url = _first(md, "url")
        mentions, slim = [], []
        for p in people:
            name = clean_name(p.get("legalName"))
            if not name:
                continue
            country = country_iso2(((p.get("postalAddress") or {}).get("countryCode") or {}).get("abbreviation"))
            kind = p.get("type") or "OTH"
            flags = [f"ORG_TYPE:{kind}"]
            if p.get("role") == "coordinator":
                flags.append("EDF_COORDINATOR")
            if p.get("sme") is True:
                flags.append("SME")
            pic = str(p.get("pic") or "").strip()
            mentions.append(Mention(
                name=name, country=country,
                city=None if country == "UA" else ((p.get("postalAddress") or {}).get("city") or None),
                website=_website(p.get("webLink")),
                identifiers=[("EU_PIC", pic)] if pic.isdigit() else [],
                flags=flags, supply_role="RND" if kind in RESEARCH_TYPES else None,
                signals=[SignalIn(type="grant", counterpart=f"{BUYER} {call or ''}".strip(), date=date, url=url,
                                  layer="A")]))
            slim.append({"pic": pic or None, "name": name, "country": country, "role": p.get("role"),
                         "type": kind, "sme": p.get("sme"), "eu_contribution": p.get("eucontribution")})
        if not mentions:
            return None
        # Payload without street, postal code or coordinates of participants.
        payload = {"projectId": project_id, "acronym": _first(md, "acronym"), "call": call,
                   "topicId": _first(md, "topicId"), "signed": date, "status": _first(md, "status"),
                   "eu_contribution": _first(md, "euContributionAmount"), "url": url, "participants": slim}
        return Record(SOURCE, str(project_id), payload, mentions)
