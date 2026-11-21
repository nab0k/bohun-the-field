"""National company registers for the legal-identity step (B2CI), design B v1 slice 3.

One result shape for every country, the same as sources/ares.py uses for Czechia:
  {"decision": "matched", "scheme", "id", "legal_name", "founded", "city", "activity", "source_url"}
  {"decision": "disputed", "candidates": [ids]}      several entities share the name (N08)
  {"decision": "not_found", "nearest": [names]}
Only an exact match of the normalised legal name is accepted (normalize.name_norm); a brand or a
look-alike name never resolves. Free public APIs without key, checked live on 21.11.2026:
  NO  Brønnøysund Enhetsregisteret  data.brreg.no            search by name
  FR  recherche-entreprises         recherche-entreprises.api.gouv.fr  search by name
  FI  PRH YTJ open data v3          avoindata.prh.fi          search by name
  PL  KRS                           api-krs.ms.gov.pl         lookup by KRS number only (no name search);
                                    the number comes from GLEIF registeredAs and is confirmed here
Every HTTP call goes through `opener` so tests run without network.
"""
import json
import urllib.parse
import urllib.request

from ..normalize import name_norm

UA = {"User-Agent": "BOHUN-research/1.0", "Accept": "application/json"}


def _get(url, opener):
    req = urllib.request.Request(url, headers=UA)
    with opener(req, timeout=30) as r:
        return json.load(r)


def _decide(name, rows, key):
    """rows: candidate dicts already in result shape; key(row) -> registered name."""
    want = name_norm(name)
    hits = [r for r in rows if name_norm(key(r)) == want]
    ids = sorted({h["id"] for h in hits})
    if len(ids) == 1:
        return {"decision": "matched", **hits[0]}
    if len(ids) > 1:
        return {"decision": "disputed", "candidates": ids}
    return {"decision": "not_found", "nearest": [key(r) for r in rows[:3]]}


# --- Norway ---------------------------------------------------------------------------------------
NO_SEARCH = "https://data.brreg.no/enhetsregisteret/api/enheter?navn={q}&size=25"
NO_DETAIL = "https://data.brreg.no/enhetsregisteret/api/enheter/{id}"
NO_SKIP_FORMS = {"UTLA"}  # foreign entity registered in Norway: not the Norwegian legal entity


def no_rows(data):
    out = []
    for e in (data.get("_embedded") or {}).get("enheter", []):
        if (e.get("organisasjonsform") or {}).get("kode") in NO_SKIP_FORMS or e.get("slettedato") or e.get("konkurs"):
            continue
        out.append({"scheme": "NO_ORGNR", "id": e["organisasjonsnummer"], "legal_name": e.get("navn"),
                    "founded": e.get("stiftelsesdato"), "city": (e.get("forretningsadresse") or {}).get("poststed"),
                    "activity": [c for c in [(e.get(k) or {}).get("kode") for k in ("naeringskode1", "naeringskode2")] if c],
                    "source_url": NO_DETAIL.format(id=e["organisasjonsnummer"])})
    return out


def resolve_no(name, opener=urllib.request.urlopen):
    return _decide(name, no_rows(_get(NO_SEARCH.format(q=urllib.parse.quote(name)), opener)), lambda r: r["legal_name"])


# --- France ---------------------------------------------------------------------------------------
FR_SEARCH = "https://recherche-entreprises.api.gouv.fr/search?q={q}&per_page=25"
FR_PERSON = {"1000"}  # nature_juridique of an individual entrepreneur: never a company card
FR_NO_DATE = {"1900-01-01"}  # placeholder creation date in the register


def fr_rows(data):
    out = []
    for r in data.get("results", []):
        if r.get("nature_juridique") in FR_PERSON or r.get("etat_administratif") != "A":
            continue
        name = r.get("nom_raison_sociale") or r.get("nom_complet")
        created = r.get("date_creation")
        out.append({"scheme": "FR_SIREN", "id": r["siren"], "legal_name": name,
                    "founded": None if created in FR_NO_DATE else created,
                    "city": (r.get("siege") or {}).get("libelle_commune"),
                    "activity": [a for a in [r.get("activite_principale")] if a],
                    "source_url": FR_SEARCH.format(q=r["siren"]).replace("&per_page=25", "")})
    return out


def resolve_fr(name, opener=urllib.request.urlopen):
    return _decide(name, fr_rows(_get(FR_SEARCH.format(q=urllib.parse.quote(name)), opener)), lambda r: r["legal_name"])


# --- Finland --------------------------------------------------------------------------------------
FI_SEARCH = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies?name={q}"
FI_DETAIL = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies?businessId={id}"


def fi_rows(data):
    """Only the current registered name counts (type '1' without endDate); a former name may belong
    to another business ID (e.g. 'Patria Oyj' 1997-2011 is now Patria Finance Oyj)."""
    out = []
    for c in data.get("companies", []):
        if c.get("endDate"):
            continue
        current = [n["name"] for n in c.get("names", []) if str(n.get("type")) == "1" and not n.get("endDate")]
        if not current:
            continue
        bid = c["businessId"]["value"]
        city = None
        for a in c.get("addresses", []):
            for p in a.get("postOffices", []):
                if str(p.get("languageCode")) == "1":
                    city = city or p.get("city")
        line = (c.get("mainBusinessLine") or {}).get("type")
        out.append({"scheme": "FI_YTUNNUS", "id": bid, "legal_name": current[0],
                    "founded": c["businessId"].get("registrationDate"), "city": city.title() if city else None,
                    "activity": [line] if line else [], "source_url": FI_DETAIL.format(id=bid)})
    return out


def resolve_fi(name, opener=urllib.request.urlopen):
    return _decide(name, fi_rows(_get(FI_SEARCH.format(q=urllib.parse.quote(name)), opener)), lambda r: r["legal_name"])


# --- Poland ---------------------------------------------------------------------------------------
PL_DETAIL = "https://api-krs.ms.gov.pl/api/krs/OdpisAktualny/{id}?rejestr={reg}&format=json"


def pl_row(data, krs, reg):
    o = data.get("odpis") or {}
    d1 = (o.get("dane") or {}).get("dzial1") or {}
    sub = d1.get("danePodmiotu") or {}
    seat = (d1.get("siedzibaIAdres") or {}).get("siedziba") or {}
    act = (((o.get("dane") or {}).get("dzial3") or {}).get("przedmiotDzialalnosci") or {}).get("przedmiotPrzewazajacejDzialalnosci") or []
    reg_date = (o.get("naglowekA") or {}).get("dataRejestracjiWKRS")  # dd.mm.yyyy: date of entry into KRS
    founded = "-".join(reversed(reg_date.split("."))) if reg_date else None
    return {"scheme": "PL_KRS", "id": krs, "legal_name": sub.get("nazwa"), "founded": founded,
            "city": (seat.get("miejscowosc") or "").title() or None,
            "activity": [f"{a.get('kodDzial')}.{a.get('kodKlasa')}{a.get('kodPodklasa') or ''}" for a in act],
            "source_url": PL_DETAIL.format(id=krs, reg=reg)}


def lookup_pl(krs, opener=urllib.request.urlopen):
    """KRS extract by number; entrepreneurs (P) first, then associations (S). None if not found."""
    krs = str(krs).zfill(10)
    for reg in ("P", "S"):
        try:
            data = _get(PL_DETAIL.format(id=krs, reg=reg), opener)
        except Exception:
            continue
        if data.get("odpis"):
            return pl_row(data, krs, reg)
    return None


def resolve_pl(name, krs, opener=urllib.request.urlopen):
    """Confirm a KRS number (from GLEIF or a counterparty document) against the register name."""
    if not krs:
        return {"decision": "not_found", "nearest": [], "reason": "no KRS number: KRS has no name search"}
    row = lookup_pl(krs, opener)
    if not row:
        return {"decision": "not_found", "nearest": [], "reason": f"KRS {krs} not in register"}
    return _decide(name, [row], lambda r: r["legal_name"])


RESOLVERS = {"NO": resolve_no, "FR": resolve_fr, "FI": resolve_fi}


def resolve(name, country, opener=urllib.request.urlopen, registered_as=None):
    """Country router. CZ stays in sources/ares.py; PL needs `registered_as` (KRS number)."""
    if country == "PL":
        return resolve_pl(name, registered_as, opener)
    if country == "CZ":
        from . import ares
        r = ares.resolve(name, opener=opener)
        if r.get("decision") == "matched":
            r = {"decision": "matched", "scheme": "CZ_ICO", "id": r["ico"], "legal_name": r["legal_name"],
                 "founded": r.get("founded"), "city": r.get("city"), "activity": r.get("nace", []),
                 "source_url": r["source_url"]}
        return r
    fn = RESOLVERS.get(country)
    if not fn:
        return {"decision": "not_found", "nearest": [], "reason": f"no free register API for {country}"}
    return fn(name, opener)
