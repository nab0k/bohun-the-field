"""TED (EU procurement) award notices: winners of defence procurement (no key).

Checked 2026-10-18: `organisation-name-tenderer` / `organisation-country-tenderer` /
`organisation-identifier-tenderer` are returned for can-* notices; names may carry "EN_"/"DE_" prefixes;
identifiers are sometimes TED UUIDs and sometimes national register numbers; some notices name no winner.
A CPV family query also returns civil items (35110000 fire-fighting etc.), so records are kept only if
at least one CPV is in a defence sub-family or the buyer is a known defence procurement agency.
"""
import re

from ..model import Mention, Record, SignalIn
from ..normalize import clean_name, country_iso2
from ..taxonomy import is_defence_cpv

API = "https://api.ted.europa.eu/v3/notices/search"
SOURCE = "ted_awards"
FIELDS = ["publication-number", "notice-type", "publication-date", "buyer-name", "buyer-country",
          "organisation-name-tenderer", "organisation-identifier-tenderer", "organisation-country-tenderer",
          "classification-cpv"]
DEFAULT_QUERY = ("classification-cpv IN (35300000 35400000 35500000 35600000 35700000 73420000 73430000) "
                 "AND notice-type IN (can-standard can-social can-desg) AND publication-date >= {since}")
DEFENCE_BUYER_HINTS = re.compile(
    r"armasuisse|agencja uzbrojenia|ministry of defen|ministerstwo obrony|bundesamt für ausrüstung|baainbw|"
    r"direction générale de l'armement|\bdga\b|forsvar|puolustus|defence equipment|\bmod\b|ministerie van defensie|"
    r"ministero della difesa|ministerio de defensa|ocrsg|nspa|\bocar\b|european defence agency", re.I)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _texts(value):
    """TED multilingual field -> list of strings, preferring English."""
    if isinstance(value, dict):
        for lang in ("eng", "ENG", "en"):
            if lang in value:
                return list(value[lang])
        return list(next(iter(value.values()), []))
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)] if value else []


class Awards:
    source = SOURCE

    def __init__(self, since="20240101", query=DEFAULT_QUERY, page_size=100):
        self.since, self.query, self.page_size = since, query, page_size

    def fetch(self, http, cursor, limit):
        """Cursor = TED iterationNextToken (ITERATION pagination)."""
        records, token = [], cursor
        while len(records) < limit:
            body = {"query": self.query.format(since=self.since), "fields": FIELDS,
                    "limit": min(self.page_size, limit - len(records)), "paginationMode": "ITERATION"}
            if token:
                body["iterationNextToken"] = token
            data = http.post_json(API, body) or {}
            if data.get("timedOut"):
                raise RuntimeError("TED search timed out")
            notices = data.get("notices") or []
            for n in notices:
                n = {k: v for k, v in n.items() if k != "links"}
                rec = self.parse(n)
                if rec:
                    records.append(rec)
            token = data.get("iterationNextToken")
            if not notices or not token:
                return records, None
        return records, token

    @staticmethod
    def parse(notice):
        cpvs = sorted(set(notice.get("classification-cpv") or []))
        buyer = "; ".join(_texts(notice.get("buyer-name")))
        defence = any(is_defence_cpv(c) for c in cpvs) or bool(DEFENCE_BUYER_HINTS.search(buyer))
        names = [clean_name(n) for n in _texts(notice.get("organisation-name-tenderer"))]
        if not defence or not names:
            return None  # civil item, or no winner published
        ids = notice.get("organisation-identifier-tenderer") or []
        countries = notice.get("organisation-country-tenderer") or []
        pub = notice.get("publication-number")
        url = f"https://ted.europa.eu/en/notice/-/detail/{pub}" if pub else None
        date = (notice.get("publication-date") or "")[:10] or None
        defence_codes = [("CPV", c) for c in cpvs if is_defence_cpv(c)]
        mentions = []
        for i, name in enumerate(names):
            ident = ids[i] if i < len(ids) and len(ids) == len(names) else None
            country = country_iso2(countries[i]) if i < len(countries) and len(countries) == len(names) else (
                country_iso2(countries) if len(set(countries)) == 1 else None)
            identifiers = []
            if ident:
                scheme = "TED_ORG" if _UUID.match(ident) else f"NATIONAL:{country or 'XX'}"
                identifiers.append((scheme, ident.strip()))
            mentions.append(Mention(
                name=name, country=country, identifiers=identifiers, codes=defence_codes,
                signals=[SignalIn(type="tender_award", counterpart=buyer or None, date=date, url=url)]))
        return Record(SOURCE, pub, notice, mentions)
