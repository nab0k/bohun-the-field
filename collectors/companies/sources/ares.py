"""ARES (Czech register of economic entities) — legal identity for Czech candidates (B2CI, slice 2).

Free public API without key (operating conditions limit request volume; licence text not found on
20.11.2026, so the data are used for internal identity checks; the site shows only short quotes).
"""
import json
import re
import urllib.request

SEARCH = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/vyhledat"
DETAIL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}"
UA = {"User-Agent": "BOHUN-research/1.0", "Content-Type": "application/json"}
_FORM = re.compile(r",?\s*(a\.\s?s\.|s\.\s?r\.\s?o\.|spol\.\s?s\s?r\.\s?o\.|v\.\s?o\.\s?s\.|k\.\s?s\.|s\.\s?p\.|z\.\s?s\.)\s*$", re.I)


def norm(name: str) -> str:
    n = _FORM.sub('', (name or '').strip())
    return re.sub(r'[^0-9a-zá-ž]+', ' ', n.lower()).strip()


def search(name: str, opener=urllib.request.urlopen, limit=25):
    body = json.dumps({"obchodniJmeno": name, "pocet": limit}).encode()
    req = urllib.request.Request(SEARCH, data=body, headers=UA, method="POST")
    with opener(req, timeout=30) as r:
        data = json.load(r)
    return data.get("ekonomickeSubjekty", [])


def match(name: str, results):
    """Exact normalised-name match only (N08). Several equal names -> disputed, none -> not_found."""
    want = norm(name)
    hits = [r for r in results if norm(r.get("obchodniJmeno", "")) == want]
    if len(hits) == 1:
        h = hits[0]
        return {"decision": "matched", "ico": h["ico"], "legal_name": h["obchodniJmeno"],
                "founded": h.get("datumVzniku"), "city": (h.get("sidlo") or {}).get("nazevObce"),
                "nace": h.get("czNace", [])[:5], "source_url": DETAIL.format(ico=h["ico"])}
    if len(hits) > 1:
        return {"decision": "disputed", "candidates": [h["ico"] for h in hits]}
    return {"decision": "not_found", "nearest": [r.get("obchodniJmeno") for r in results[:3]]}


def resolve(name: str, opener=urllib.request.urlopen):
    """Try the listed name, then spaced hyphens, then the name without the legal form.
    Every attempt still needs an exact normalised-name match (N08); the first decisive answer wins."""
    base = _FORM.sub('', name).strip(' ,')
    tries = [name, name.replace('-', ' - '), base]
    seen, last = set(), {"decision": "not_found", "nearest": []}
    for q in tries:
        if q in seen or not q:
            continue
        seen.add(q)
        res = match(name, search(q, opener=opener))
        if res["decision"] != "not_found":
            return res
        last = res
    return last
