"""US Department of Defense contracts of one company from USAspending.gov (no key) — EXP-035, owner 24.11.2026
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
PLAIN = {'THE', 'IS', 'FOR', 'AND', 'OF', 'TO', 'A', 'AN', 'IN', 'ON', 'WITH', 'BY', 'AT', 'AS', 'OR', 'THIS', 'THAT', 'FROM',
         'ARE', 'BE', 'ITS', 'ALL', 'NEW', 'BASE', 'LONG', 'LEAD', 'LOT', 'PER', 'ONE', 'TWO', 'YEAR', 'USE', 'NOT', 'ANY'}
BOILER = re.compile(r'^(the )?purpose of th(is|e) (modification|contract|action|order) is to (award |provide |exercise )?', re.I)


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
    flt = {'time_period': [{'start_date': start, 'end_date': end}], 'agencies': [DOD], 'award_type_codes': CONTRACTS,
           'recipient_search_text': rec['ueis'][:100]}
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
    return parse(rec, buyers, count, awards, start, end, today)


def parse(rec, buyers, count, awards, start, end, today=None):
    mine = [a for a in awards if a.get('Recipient UEI') in set(rec.get('ueis') or [rec['uei']])] or awards
    rid = next((a.get('recipient_id') for a in mine if a.get('recipient_id')), None)
    money = [{'name': b['name'], 'amount': round(float(b['amount']), 2)} for b in buyers if b.get('name') and (b.get('amount') or 0) > 0]
    total = round(sum(b['amount'] for b in money), 2)
    return {'recipient': rec['recipient_name'], 'uei': rec['uei'], 'ueis': len(rec.get('ueis') or [rec['uei']]), 'partial': bool(rec.get('partial')),
            'profile': f'{SITE}/recipient/{rid}/latest' if rid else f'{SITE}/search', 'period': period(start, end, today),
            'total': total, 'count': int(count), 'buyers': money,
            'awards': [{'ref': a['Award ID'], 'buyer': a.get('Awarding Sub Agency'), 'title': title(a.get('Description'), a['Award ID']),
                        'amount': round(float(a.get('Award Amount') or 0), 2), 'date': a.get('Start Date'), 'end': a.get('End Date'),
                        'url': f'{SITE}/award/{a["generated_internal_id"]}'} for a in mine if a.get('generated_internal_id')]}
