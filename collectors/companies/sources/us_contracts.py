"""US Department of Defense contracts of one company — defence goods and R&D only (PSC groups, owner 25.11.2026) from USAspending.gov (no key) — EXP-035, owner 24.11.2026
«Yes, build them»: cards of companies without EDF show what the defence buyers spent with them.

Per company: the recipient (the exact name among the award rows of the period; the UEI seen most often), its UEI,
the money the DoD obligated to it in the period by awarding sub-agency, the number of contracts with activity, and
the largest contracts (description, buyer, total value, dates, award page). Lessons of the 24.11 trial
(EXP-035 NON_EDF_CARDS_20261124.md): "Award Amount" is the value over the whole life of the contract, not of the
period — the period money comes from the sub-agency breakdown; old records carry a coded string instead of a
description; the recipient text filter also matches other companies whose name contains it, so the UEI is used
as the filter once known.
"""
import json
import re
import time
import urllib.error
import urllib.request

from ..normalize import name_norm

API = 'https://api.usaspending.gov/api/v2'
SITE = 'https://www.usaspending.gov'
UA = {'User-Agent': 'BOHUN-research/1.0', 'Content-Type': 'application/json'}
DOD = {'type': 'awarding', 'tier': 'toptier', 'name': 'Department of Defense'}
CONTRACTS = ['A', 'B', 'C', 'D']
# Defence goods and defence R&D only (owner 25.11.2026 «Yes, defence codes only»: CDW Government, an IT reseller,
# had a card from DoD purchases). PSC groups: 10 weapons, 11 nuclear ordnance, 12 fire control, 13 ammunition and
# explosives, 14 guided missiles, 15 aircraft, 16 aircraft components, 17 aircraft launching and landing equipment,
# 18 space vehicles, 19 ships, 20 ship equipment, 23 vehicles, 25 vehicle components, 28 engines, 29 engine
# accessories, 58 communication and detection, 59 electronic components; R&D: AC defence systems, AR space. The groups
# of the procurement collector (usaspending.py) plus 11, 12, 17, 18, 20, 25, 29, AR after the live check of 25.11.2026
# (LOC Performance, Bradley components, had 5 % under the narrower list).
DEFENCE_PSC = ['10', '11', '12', '13', '14', '15', '16', '17', '18', '19', '20', '23', '25', '28', '29', '58', '59', 'AC', 'AR']
PSC = {'require': [['Research and Development' if g[:1].isalpha() else 'Product', g] for g in DEFENCE_PSC]}
# A company is shown as a defence supplier only when at least this share of what the DoD paid it is under those
# codes (25.11.2026, live: CDW Government 3.5 %, Lockheed Martin 90 %).
MIN_SHARE = 0.25
PLAIN = {'THE', 'IS', 'FOR', 'AND', 'OF', 'TO', 'A', 'AN', 'IN', 'ON', 'WITH', 'BY', 'AT', 'AS', 'OR', 'THIS', 'THAT', 'FROM',
         'ARE', 'BE', 'ITS', 'ALL', 'NEW', 'BASE', 'LONG', 'LEAD', 'LOT', 'PER', 'ONE', 'TWO', 'YEAR', 'USE', 'NOT', 'ANY'}
BOILER = re.compile(r'^(the )?purpose of th(is|e) (modification|contract|action|order) is to (award |provide |exercise )?', re.I)


def city(recipient_id, opener=urllib.request.urlopen):
    """The city of the recipient profile (only the city: never the street or the postal code), or None."""
    if not recipient_id:
        return None
    try:
        req = urllib.request.Request(f'{API}/recipient/{recipient_id}/', headers={'User-Agent': UA['User-Agent']})
        with opener(req, timeout=60) as r:
            c = ((json.load(r) or {}).get('location') or {}).get('city_name')
    except Exception:  # noqa: BLE001 — the city is optional
        return None
    return c.title() if c and c.isupper() else c


def _post(path, body, opener=urllib.request.urlopen, tries=3, pause=5):
    """POST with retries on 429 / 5xx (the API answers 503 under load now and then)."""
    for n in range(tries):
        req = urllib.request.Request(f'{API}{path}', data=json.dumps(body).encode(), headers=UA, method='POST')
        try:
            with opener(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or n == tries - 1:
                raise
            time.sleep(pause * (n + 1))
        except (TimeoutError, urllib.error.URLError):
            if n == tries - 1:
                raise
            time.sleep(pause * (n + 1))


def match(name, rows):
    """The recipient whose name equals `name` (normalised, legal forms ignored) among award rows: all its UEIs (a large
    company has one per registered site — Lockheed Martin had hundreds), the one seen most often first."""
    key = name_norm(name)
    seen = {}
    for r in rows:
        if name_norm(r.get('Recipient Name') or '') == key and r.get('Recipient UEI'):
            seen.setdefault(r['Recipient UEI'], [r['Recipient Name'], 0])[1] += 1
    if not seen:
        return None
    ueis = sorted(seen, key=lambda u: -seen[u][1])
    return {'uei': ueis[0], 'ueis': ueis, 'recipient_name': seen[ueis[0]][0]}


def share(ueis, start, end, opener=urllib.request.urlopen):
    """(DoD money under the defence codes, all DoD money) to these recipients in [start, end]."""
    out = []
    for psc in (PSC, None):
        f = {'time_period': [{'start_date': start, 'end_date': end}], 'agencies': [DOD], 'award_type_codes': CONTRACTS,
             'recipient_search_text': list(ueis)[:100]}
        if psc:
            f['psc_codes'] = psc
        rows = _post('/search/spending_by_category/recipient/', {'filters': f, 'limit': 100}, opener).get('results') or []
        out.append(round(sum(float(r.get('amount') or 0) for r in rows), 2))
    return out[0], out[1]


def title(desc, award_id):
    d = ' '.join((desc or '').split())
    d = BOILER.sub('', d)
    if '!' in d or len(d) < 6:
        return f'Contract {award_id}'
    if d.isupper():  # sentence case for plain words; acronyms and designations (LRIP, VTOL, F-35A) stay
        d = ' '.join(w.lower() if w.isalpha() and (len(w) > 4 or w in PLAIN) else w for w in d.split())
        d = d[:1].upper() + d[1:]
    return d[:120]


def period(start, end, today=None):
    """'FY2025–26' for 2024-10-01 … 2026-09-30; with ', to 24 Nov 2026' when the window is still open."""
    a, b = int(start[:4]) + 1, int(end[:4])
    label = f'FY{a}' if a == b else f'FY{a}–{str(b)[2:]}'
    if today and today.isoformat() < end:
        label += f', to {today.day} {today.strftime("%b %Y")}'
    return label


def fetch(name, start, end, opener=urllib.request.urlopen, top=8, today=None):
    """None when no recipient of that exact name; else the summary of the DoD contracts in [start, end]."""
    probe = {'time_period': [{'start_date': start, 'end_date': end}], 'agencies': [DOD], 'award_type_codes': CONTRACTS,
             'recipient_search_text': [name]}
    rows = []
    for page in (1, 2, 3):
        got = _post('/search/spending_by_award/', {'filters': probe, 'limit': 100, 'page': page,
                    'fields': ['Recipient Name', 'Recipient UEI']}, opener).get('results') or []
        rows += got
        if len(got) < 100:
            break
    rec = match(name, rows)
    if not rec:
        return None
    try:
        defence, every = share(rec['ueis'], start, end, opener)
    except urllib.error.HTTPError as e:  # many UEIs time out under load (Raytheon): the main ones, as for the breakdown
        if e.code < 500 or len(rec['ueis']) <= 10:
            raise
        defence, every = share(rec['ueis'][:10], start, end, opener)
    if not every or defence / every < MIN_SHARE:  # mostly non-defence purchases: no defence summary at all
        return {'recipient': rec['recipient_name'], 'uei': rec['uei'], 'total': 0, 'non_defence': True,
                'defence_share': round(defence / every, 3) if every else 0.0, 'all_dod': every}
    flt = {'time_period': [{'start_date': start, 'end_date': end}], 'agencies': [DOD], 'award_type_codes': CONTRACTS,
           'recipient_search_text': rec['ueis'][:100], 'psc_codes': PSC}
    try:
        buyers = _post('/search/spending_by_category/awarding_subagency/', {'filters': flt, 'limit': 10}, opener).get('results') or []
    except urllib.error.HTTPError as e:  # the breakdown over many UEIs times out under load: the main UEIs only, marked partial
        if e.code < 500 or len(rec['ueis']) <= 10:
            raise
        rec['ueis'], rec['partial'] = rec['ueis'][:10], True
        flt['recipient_search_text'] = rec['ueis']
        buyers = _post('/search/spending_by_category/awarding_subagency/', {'filters': flt, 'limit': 10}, opener).get('results') or []
    count = (_post('/search/spending_by_award_count/', {'filters': flt}, opener).get('results') or {}).get('contracts') or 0
    awards = _post('/search/spending_by_award/', {'filters': flt, 'limit': top, 'sort': 'Award Amount', 'order': 'desc',
                   'fields': ['Award ID', 'Recipient Name', 'Recipient UEI', 'recipient_id', 'Award Amount', 'Awarding Sub Agency',
                              'Start Date', 'End Date', 'Description', 'generated_internal_id']}, opener).get('results') or []
    d = parse(rec, buyers, count, awards, start, end, today)
    d['city'] = city(d.pop('_rid', None), opener)
    d['defence_share'], d['all_dod'] = round(defence / every, 3), every
    return d


def parse(rec, buyers, count, awards, start, end, today=None):
    mine = [a for a in awards if a.get('Recipient UEI') in set(rec.get('ueis') or [rec['uei']])] or awards
    rid = next((a.get('recipient_id') for a in mine if a.get('recipient_id')), None)
    money = [{'name': b['name'], 'amount': round(float(b['amount']), 2)} for b in buyers if b.get('name') and (b.get('amount') or 0) > 0]
    total = round(sum(b['amount'] for b in money), 2)
    return {'_rid': rid, 'recipient': rec['recipient_name'], 'uei': rec['uei'], 'ueis': len(rec.get('ueis') or [rec['uei']]), 'partial': bool(rec.get('partial')),
            'profile': f'{SITE}/recipient/{rid}/latest' if rid else f'{SITE}/search', 'period': period(start, end, today),
            'total': total, 'count': int(count), 'buyers': money,
            'awards': [{'ref': a['Award ID'], 'buyer': a.get('Awarding Sub Agency'), 'title': title(a.get('Description'), a['Award ID']),
                        'amount': round(float(a.get('Award Amount') or 0), 2), 'date': a.get('Start Date'), 'end': a.get('End Date'),
                        'url': f'{SITE}/award/{a["generated_internal_id"]}'} for a in mine if a.get('generated_internal_id')]}
