"""Government of Canada contracts of one company — Proactive Publication, Contracts over $10,000 (open.canada.ca,
Open Government Licence – Canada, CKAN datastore, no key). EXP-035, owner 24.11.2026 «Yes, build them».

Lessons of the 24.11 trial (EXP-035 NON_EDF_CARDS_20261124.md):
- full-text search is switched off for this resource (over 100 000 rows): only an exact `vendor_name` filter works,
  so several spellings of the name are tried (CAE had five: "CAE INC.", "CAE Inc." …);
- a multi-year contract is reported again every quarter under a new reference number with the same date, buyer,
  description and value: one contract is kept once (the latest report); the plain sum of all rows overstates;
- `description_en` is the economic object (a spending category such as "Weapons (including parts)"), not the
  subject of the contract: it is shown as the category.
Never stored: the vendor postal code and any person (former public servant flags are not read).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = 'https://open.canada.ca/data/api/action/datastore_search'
RESOURCE = 'fac950c0-00d5-4ec1-a4d3-9cbebf98a305'
DATASET = 'https://open.canada.ca/data/en/dataset/d8f85d91-7dec-4fd1-8055-483b77225d8b'
RECORD = 'https://search.open.canada.ca/contracts/record/{org},{ref}'
UA = {'User-Agent': 'BOHUN-research/1.0'}
DEFENCE_ORGS = {'National Defence', 'Communications Security Establishment Canada', 'Canadian Security Intelligence Service'}
FORMS = ('Inc.', 'Inc', 'INC.', 'INC', 'Ltd.', 'Ltd', 'LTD.', 'LTD', 'Limited', 'LIMITED', 'Corporation', 'CORPORATION', 'Corp.', 'ULC', 'Ltée')


def variants(name, aliases=()):
    """Exact spellings to try: the name and aliases as given, upper case, and with the usual legal forms swapped."""
    out = []

    def add(v):
        v = ' '.join(v.split()).strip(' ,')
        if v and v not in out:
            out.append(v)
    for n in [name, *aliases]:
        add(n)
        add(n.upper())
        base = n
        for f in FORMS:
            if base.endswith(' ' + f):
                base = base[:-len(f) - 1].rstrip(' ,')
                break
        if base != n:
            add(base)
            add(base.upper())
            for f in ('Inc.', 'INC.', 'Inc', 'INC', 'Ltd.', 'LTD.', 'Limited'):
                add(f'{base} {f}')
    return out[:12]


def _get(url, opener, tries=3):
    for n in range(tries):
        try:
            with opener(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == tries - 1:
                raise
            time.sleep(5 * (n + 1))
        except (TimeoutError, urllib.error.URLError):
            if n == tries - 1:
                raise
            time.sleep(5 * (n + 1))


def fetch(name, aliases=(), opener=urllib.request.urlopen, pause=0.5):
    """Rows of every exact spelling that has contracts (spellings with none are skipped)."""
    rows = []
    for v in variants(name, aliases):
        q = urllib.parse.quote(json.dumps({'vendor_name': v}))
        got = (_get(f'{API}?resource_id={RESOURCE}&limit=500&filters={q}', opener).get('result') or {}).get('records') or []
        rows += got
        time.sleep(pause)
    return rows


def org(x):
    return (x.get('owner_org_title') or '').split('|')[0].strip()


def summarise(rows, top=8):
    """None without rows; else unique contracts, the buyers by number of contracts, the largest contracts."""
    if not rows:
        return None
    uniq = {}
    for x in rows:
        k = (x.get('contract_date'), org(x), x.get('description_en'), x.get('contract_value'))
        if k not in uniq or str(x.get('reference_number')) > str(uniq[k].get('reference_number')):
            uniq[k] = x
    items = list(uniq.values())
    value = lambda x: float(x.get('contract_value') or 0)  # noqa: E731
    buyers = {}
    for x in items:
        b = buyers.setdefault(org(x), {'name': org(x), 'n': 0, 'largest': 0.0})
        b['n'] += 1
        b['largest'] = max(b['largest'], value(x))
    dates = sorted(x['contract_date'] for x in items if x.get('contract_date'))
    return {'vendors': sorted({x.get('vendor_name') for x in items if x.get('vendor_name')}), 'count': len(items), 'rows': len(rows),
            'first': dates[0] if dates else None, 'last': dates[-1] if dates else None,
            'defence': sum(1 for x in items if org(x) in DEFENCE_ORGS),
            'buyers': sorted(buyers.values(), key=lambda b: (-b['n'], -b['largest']))[:8],
            'contracts': [{'ref': x.get('reference_number'), 'buyer': org(x), 'vendor': x.get('vendor_name'),
                           'category': (x.get('description_en') or '').strip() or None, 'amount': value(x), 'currency': 'CAD',
                           'date': x.get('contract_date'), 'end': x.get('delivery_date') or None,
                           'url': RECORD.format(org=x.get('owner_org'), ref=x.get('reference_number')) if x.get('owner_org') else DATASET}
                          for x in sorted(items, key=lambda x: -value(x))[:top]]}
