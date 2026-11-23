"""Wikidata and GLEIF name search: a free route for companies without a national register route
(IT, DE, US and the companies without a country; B4CE-04, EXP-033 FREE_SOURCES_CHECK_20261123.md).

Wikidata gives the country, the website, the LEI, the parent and the coordinates of many larger companies, and a
shared identifier (QID) for spotting duplicates. GLEIF full-text search gives an LEI by name for any country.
Both are free and need no key; both are asked politely (User-Agent, one request at a time).

A hit counts only when the name equals the company name up to the legal form (name_norm) — the rule N08 of the
national registers — and, for Wikidata, when the item is a business. A known country must agree.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..normalize import name_norm

UA = {'User-Agent': 'BOHUN-research/1.0 (defence company catalogue; contact via repository nab0k/bohun-the-field)',
      'Accept': 'application/json'}
WD_SEARCH = ('https://www.wikidata.org/w/api.php?action=wbsearchentities&search={q}&language=en&uselang=en'
             '&type=item&limit=10&format=json')
WD_ENTITIES = 'https://www.wikidata.org/w/api.php?action=wbgetentities&ids={ids}&props=claims&format=json'
# The query service (SPARQL) was rate-limited to one request a minute during an outage on 23.11.2026, so the item
# details come from the ordinary API. "A business" = an instance of one of these classes, or an item that carries a
# legal identifier (LEI, OpenCorporates, Companies House, SEC CIK).
BUSINESS = {'Q4830453', 'Q783794', 'Q891723', 'Q6881511', 'Q1589009', 'Q658255', 'Q167037', 'Q134161', 'Q1664720',
            'Q460178', 'Q15711870', 'Q2221906', 'Q1058914', 'Q210167', 'Q166280', 'Q1137109', 'Q1631129', 'Q1762059',
            'Q19862406', 'Q187652', 'Q936518', 'Q15265344', 'Q18388277', 'Q2385804', 'Q1616075', 'Q11032',
            'Q190117', 'Q1030034', 'Q17377208', 'Q3152824', 'Q22687', 'Q1934969', 'Q2089281'}
LEGAL_IDS = ('P1278', 'P1320', 'P2622', 'P5531')
GLEIF_SEARCH = ('https://api.gleif.org/api/v1/lei-records?filter%5Bfulltext%5D={q}{country}'
                '&filter%5Bentity.status%5D=ACTIVE&page%5Bsize%5D=10')
PAUSE = 1.0  # seconds between requests: both services ask for moderate automated use


# legal forms of one kind: "Vector Technologies, Inc." and "Vector Technologies LLC" are two different legal entities
_FORM_KIND = [('inc', r'inc|incorporated|corp|corporation'), ('llc', r'llc|l\.l\.c'), ('ltd', r'ltd|limited|plc'),
              ('gmbh', r'gmbh'), ('ag', r'ag|aktiengesellschaft'), ('kg', r'kg|co\.? kg'), ('spa', r's\.?p\.?a'),
              ('srl', r's\.?r\.?l'), ('sa', r's\.?a|societe anonyme'), ('sas', r's\.?a\.?s'),
              ('ab', r'ab|aktiebolag'), ('as', r'a/s|as|asa'), ('oy', r'oy|oyj'), ('bv', r'b\.?v'), ('nv', r'n\.?v')]
_FORM_RES = [(k, re.compile(r'(?<![\w])(' + rx + r')\.?(?![\w])', re.I)) for k, rx in _FORM_KIND]


def forms(name):
    return {k for k, rx in _FORM_RES if rx.search(name or '')}


def same_entity_name(a, b):
    """Equal up to the legal form, and the legal forms, when both names state one, are of the same kind."""
    if not a or not b or name_norm(a) != name_norm(b):
        return False
    fa, fb = forms(a), forms(b)
    return not (fa and fb and not fa & fb)


def _get(url, opener, tries=4):
    """GET JSON; on 429 / 503 wait (Retry-After or 5, 15, 45 s) and try again."""
    for n in range(tries):
        req = urllib.request.Request(url, headers=UA)
        try:
            with opener(req, timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or n == tries - 1:
                raise
            wait = e.headers.get('Retry-After') if e.headers else None
            time.sleep(min(int(wait), 120) if wait and wait.isdigit() else 5 * 3 ** n)


def _qid(uri):
    return (uri or '').rsplit('/', 1)[-1]


def wd_candidates(name, opener=urllib.request.urlopen):
    """[(qid, matched text)] whose label or alias equals the name up to the legal form."""
    data = _get(WD_SEARCH.format(q=urllib.parse.quote(name)), opener)
    want = name_norm(name)
    out = []
    for r in data.get('search') or []:
        text = (r.get('match') or {}).get('text') or r.get('label') or ''
        if want and same_entity_name(name, text):
            out.append((r['id'], text))
    return out


def _claims(entity, prop):
    out = []
    for c in (entity.get('claims') or {}).get(prop) or []:
        v = ((c.get('mainsnak') or {}).get('datavalue') or {}).get('value')
        if isinstance(v, dict) and 'id' in v:
            out.append(v['id'])
        elif isinstance(v, dict) and 'latitude' in v:
            out.append(f"Point({v['longitude']} {v['latitude']})")
        elif v is not None:
            out.append(v)
    return out


def _entities(ids, opener):
    out = {}
    ids = list(ids)
    for k in range(0, len(ids), 50):
        data = _get(WD_ENTITIES.format(ids='|'.join(ids[k:k + 50])), opener)
        out.update(data.get('entities') or {})
    return out


_ISO = {}


def wd_details(qids, opener=urllib.request.urlopen):
    """{qid: {isbiz, countries, leis, sites, parents, coord}} for the given items (ordinary API, not SPARQL)."""
    if not qids:
        return {}
    ents = _entities(qids, opener)
    countries = {c for e in ents.values() for c in _claims(e, 'P17')} - set(_ISO)
    if countries:
        for cid, ce in _entities(countries, opener).items():
            iso = _claims(ce, 'P297')
            _ISO[cid] = iso[0] if iso else None
    out = {}
    for qid, e in ents.items():
        out[qid] = {'isbiz': bool(set(_claims(e, 'P31')) & BUSINESS) or any(_claims(e, p) for p in LEGAL_IDS),
                    'countries': {_ISO.get(c) for c in _claims(e, 'P17') if _ISO.get(c)},
                    'leis': set(_claims(e, 'P1278')), 'sites': set(_claims(e, 'P856')),
                    'parents': set(_claims(e, 'P749')), 'coord': (_claims(e, 'P625') or [None])[0]}
    return out


def wikidata(name, country=None, opener=urllib.request.urlopen, pause=PAUSE):
    """{'decision': matched|disputed|not_found, ...}. matched: qid, country, lei, website, parent, coord."""
    try:
        cands = wd_candidates(name, opener)
        time.sleep(pause)
        details = wd_details([q for q, _ in cands], opener) if cands else {}
    except Exception as e:  # noqa: BLE001 — a failed lookup is not a finding
        return {'decision': 'error', 'reason': f'Wikidata: {type(e).__name__}'}
    hits = []
    for qid, text in cands:
        d = details.get(qid)
        if not d or not d['isbiz']:
            continue
        if country and d['countries'] and country not in d['countries']:
            continue
        hits.append((qid, text, d))
    if len(hits) > 1:
        return {'decision': 'disputed', 'candidates': [h[0] for h in hits]}
    if not hits:
        return {'decision': 'not_found'}
    qid, text, d = hits[0]
    one = lambda s: sorted(s)[0] if len(s) == 1 else None  # noqa: E731 — several values: not certain
    return {'decision': 'matched', 'qid': qid, 'name': text, 'country': one(d['countries']), 'lei': one(d['leis']),
            'website': one(d['sites']), 'parent': one(d['parents']), 'coord': d['coord'],
            'source_url': f'https://www.wikidata.org/wiki/{qid}'}


def gleif_by_name(name, country=None, opener=urllib.request.urlopen):
    """An active LEI whose legal name equals the name up to the legal form; a known country must agree."""
    url = GLEIF_SEARCH.format(q=urllib.parse.quote(name),
                              country=f'&filter%5Bentity.legalAddress.country%5D={country}' if country else '')
    try:
        data = _get(url, opener)
    except Exception as e:  # noqa: BLE001
        return {'decision': 'error', 'reason': f'GLEIF: {type(e).__name__}'}
    want = name_norm(name)
    hits = []
    for r in data.get('data') or []:
        e = (r.get('attributes') or {}).get('entity') or {}
        legal = (e.get('legalName') or {}).get('name') or ''
        others = [o.get('name') for o in e.get('otherNames') or [] if o.get('name')]
        if want and any(same_entity_name(name, n) for n in [legal] + others):
            hits.append({'lei': r['id'], 'legal_name': legal, 'country': (e.get('legalAddress') or {}).get('country'),
                         'city': ((e.get('legalAddress') or {}).get('city') or '').title() or None,
                         'source_url': f'https://api.gleif.org/api/v1/lei-records/{r["id"]}'})
    leis = sorted({h['lei'] for h in hits})
    if len(leis) == 1:
        return {'decision': 'matched', **hits[0]}
    if leis:
        return {'decision': 'disputed', 'candidates': leis}
    return {'decision': 'not_found'}


def enrich(name, country=None, opener=urllib.request.urlopen, pause=PAUSE):
    """Wikidata first, then a GLEIF name search with the known or found country. When both give an LEI and the two
    differ, the LEI is a conflict and is not used (Nikon SLM Solutions, 23.11.2026)."""
    wd = wikidata(name, country, opener, pause)
    out = {'wikidata': wd}
    found_country = country or (wd.get('country') if wd.get('decision') == 'matched' else None)
    time.sleep(pause)
    by_name = gleif_by_name(name, found_country, opener)
    wd_lei = wd.get('lei') if wd.get('decision') == 'matched' else None
    if wd_lei and by_name.get('decision') == 'matched' and by_name['lei'] != wd_lei:
        out['lei'] = {'decision': 'conflict', 'candidates': sorted({wd_lei, by_name['lei']})}
    elif by_name.get('decision') == 'matched':
        out['lei'] = by_name
    elif wd_lei:
        out['lei'] = {'decision': 'from_wikidata', 'lei': wd_lei}
    else:
        out['lei'] = by_name
    out['country'] = found_country
    return out
