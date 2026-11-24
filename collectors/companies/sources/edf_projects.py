"""Details of European Defence Fund projects for company cards (owner 23.11.2026: a programme line must say what
the project is, the company's role and money, and link to the project page — not only a number).

Source: EU Funding & Tenders Search API, the same public index as sources/edf.py (apiKey SEDIA_NONH2020_PROD,
CC BY 4.0). One request per batch of project numbers. Per project: acronym, title, topic, EU contribution,
dates, status, number of participants, coordinator; per company: its role and its EU contribution, found by its
EU PIC or, failing that, by its legal name. The other participants are kept in a short form (name, country, kind of
organisation, role, PIC) for the network on the company page (EXP-035, owner 23.11.2026); never addresses or people.
"""
import json
import urllib.request

from ..normalize import name_norm

API = 'https://api.tech.ec.europa.eu/search-api/prod/rest/search?apiKey=SEDIA_NONH2020_PROD&text=***&pageSize={n}&pageNumber=1'
PAGE = 'https://ec.europa.eu/info/funding-tenders/opportunities/portal/screen/opportunities/projects-details/44181033/{id}/EDF'
UA = 'BOHUN-research/1.0'


def _first(md, key):
    v = md.get(key)
    return (v[0] if v else None) if isinstance(v, list) else v


def _post(project_ids, opener):
    boundary = 'bohunedf'
    query = json.dumps({'bool': {'must': [{'terms': {'programAbbreviation': ['EDF']}},
                                          {'terms': {'projectId': [str(p) for p in project_ids]}}]}})
    body = ''.join(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\nContent-Type: application/json\r\n\r\n{v}\r\n'
                   for k, v in (('query', query), ('languages', '["en"]'))) + f'--{boundary}--\r\n'
    req = urllib.request.Request(API.format(n=max(10, 2 * len(project_ids))), data=body.encode(), method='POST',
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}', 'User-Agent': UA})
    with opener(req, timeout=60) as r:
        return json.load(r)


def parse(md):
    """Project details from one index entry, or None."""
    pid = _first(md, 'projectId')
    if not pid or (_first(md, 'language') or 'en') != 'en':
        return None
    try:
        parts = json.loads(_first(md, 'participants') or '[]')
    except ValueError:
        parts = []
    coord = next((p for p in parts if (p.get('role') or '').lower() == 'coordinator'), None)
    money = _first(md, 'euContributionAmount') or _first(md, 'overallBudget')
    title = ' '.join((_first(md, 'title') or '').split()) or None
    return {'project': str(pid), 'acronym': _first(md, 'acronym'), 'title': title,
            'topic': _first(md, 'topicAbbreviation'), 'call': _first(md, 'callIdentifier'),
            'action': _first(md, 'typeOfAction'), 'status': _first(md, 'status'),
            'start': (_first(md, 'startDate') or '')[:10] or None, 'end': (_first(md, 'endDate') or '')[:10] or None,
            'eu_total': float(money) if money not in (None, '', []) else None,
            'partners': len(parts) or None, 'coordinator': (coord or {}).get('legalName'),
            'url': PAGE.format(id=pid), '_participants': parts}


def fetch(project_ids, opener=urllib.request.urlopen, batch=40):
    """{project number: details} for the numbers the index knows."""
    out, ids = {}, sorted({str(p) for p in project_ids})
    for i in range(0, len(ids), batch):
        data = _post(ids[i:i + batch], opener) or {}
        for item in data.get('results') or []:
            d = parse(item.get('metadata') or {})
            if d and d['project'] not in out:
                out[d['project']] = d
    return out


KINDS = {'PRC': 'company', 'REC': 'research', 'HES': 'research', 'PUB': 'public', 'OTH': 'other'}


def short(p):
    """One participant without address, coordinates or contacts."""
    cc = ((p.get('postalAddress') or {}).get('countryCode') or {}).get('abbreviation')
    return {'name': ' '.join((p.get('legalName') or '').split()) or None, 'country': cc,
            'kind': KINDS.get(p.get('type') or '', 'other'), 'role': (p.get('role') or '').lower() or None,
            'pic': str(p['pic']) if p.get('pic') else None}


def for_company(details, pics=(), names=()):
    """The project details plus the company's role and EU contribution; the other participants in short form."""
    pics = {str(p) for p in pics}
    keys = {name_norm(n) for n in names if n}
    me = next((p for p in details.get('_participants') or [] if str(p.get('pic') or '') in pics), None) or \
        next((p for p in details.get('_participants') or [] if name_norm(p.get('legalName') or '') in keys), None)
    out = {k: v for k, v in details.items() if k != '_participants'}
    out['participants'] = [short(p) for p in details.get('_participants') or [] if p is not me]
    if me:
        out['role'] = (me.get('role') or '').lower() or None
        try:
            out['company_eu'] = float(me.get('eucontribution')) if me.get('eucontribution') not in (None, '') else None
        except (TypeError, ValueError):
            out['company_eu'] = None
    return out
