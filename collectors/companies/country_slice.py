"""Country slice of design B v1 (slice 2, Czechia): lists -> legal identity -> evidence -> level -> card.

No model step: identity comes from the national register (ARES), evidence from contract and grant
registers (TED awards, EDF grants) and the association list (AOBP); the level is decided by level.py.
A card passes the gate (design B v1) when: the legal entity is matched in the register; the level is
backed by a source other than the company itself, or it is honestly L0/L4 with a search record;
every empty field is labelled; every quote comes from a stored source record.
"""
from __future__ import annotations

import json
import re
import unicodedata

from .level import classify


def list_key(name: str) -> str:
    """Loose key for merging the three lists (selection only, never for merging companies)."""
    n = unicodedata.normalize('NFKD', name or '').encode('ascii', 'ignore').decode().lower()
    n = re.sub(r'[.,&\-–]', ' ', n)
    n = re.sub(r'\b(a\s*s|s\s*r\s*o|spol\s*s\s*r\s*o|spol|sro|as|s\s*p|int)\b', ' ', n)
    return re.sub(r'\s+', ' ', n).strip()


def claims_for(evidence: dict):
    """Structured claims for level.classify from one company's evidence bundle."""
    claims = []
    for a in evidence.get('ted', []):
        if a['defence_buyer']:
            claims.append({'kind': 'contract', 'customer': True, 'documented': True, 'source': 'government',
                           'group': 'ted', 'named': True})
        else:
            claims.append({'kind': 'relevance', 'documented': True, 'source': 'government', 'group': 'ted'})
    for g in evidence.get('edf', []):
        claims.append({'kind': 'contract', 'customer': True, 'documented': True, 'source': 'government',
                       'group': 'edf', 'named': True})
    if evidence.get('aobp'):
        claims.append({'kind': 'membership', 'source': 'self', 'group': 'aobp'})
        claims.append({'kind': 'capability_claim', 'source': 'self', 'group': 'self'})
    return claims


def card(name: str, identity: dict, evidence: dict):
    level = classify(claims_for(evidence), entity_resolved=identity.get('decision') == 'matched')
    gaps = []
    if identity.get('decision') != 'matched':
        gaps.append('identity: ' + identity.get('decision', 'not_checked'))
    independent = bool(evidence.get('ted') or evidence.get('edf'))
    honest_low = level['level'] in ('L0', 'L4')
    passes = identity.get('decision') == 'matched' and (independent or honest_low)
    return {
        'name_as_listed': name,
        'identity': identity,
        'level': level['level'], 'confidence': level['confidence'], 'rule': level['rule'],
        'explanation': level['explanation'], 'ignored': level['ignored'],
        'lists': sorted({k for k in ('aobp', 'ted', 'edf') if evidence.get(k)}),
        'programmes': [
            *[{'kind': 'tender_award', 'ref': a['notice'], 'buyer': a['buyer'], 'date': a['date'],
               'defence_buyer': a['defence_buyer'], 'url': a['url']} for a in evidence.get('ted', [])],
            *[{'kind': 'edf_grant', 'ref': g['project'], 'title': g.get('title'), 'url': g['url']}
              for g in evidence.get('edf', [])]],
        'about_quote': (evidence.get('aobp') or {}).get('evidence_quote'),
        'about_source': (evidence.get('aobp') or {}).get('list_row_ref'),
        'specialisations': (evidence.get('aobp') or {}).get('specialisations', []),
        'empty': {'products': 'not_assessed', 'technologies': 'not_assessed', 'financials': 'not_assessed',
                  'relationships': 'not_assessed', 'defence_revenue_share': 'unknown'},
        'gaps': gaps,
        'gate': 'pass' if passes else 'fail',
    }


def dump(cards, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)
