"""One company card from the queue, any country (B4CE-04, slice 3.4).

Steps, design B v1: legal identity from the national register (registries.resolve) or GLEIF; evidence
from signals already stored in companies.signal; products from the company website (products.extract);
level by level.classify; the card gate. Nothing is written here: the caller decides about the database.

Signals used (companies.signal):
  grant / A          EU defence fund project          -> contract with a defence customer (L3)
  tender_award / A   TED award, defence buyer          -> contract with a defence customer (L3)
  tender_award / C   TED award, civil security buyer   -> documented relevance (L4)
  contract / A       US federal defence contract       -> contract with a defence customer (L3); cited through
                     the USAspending recipient page when the row has no link
  association, exhibition                              -> membership: a lead, not a level (N02)
  self_declared                                        -> capability claim: candidate L4 (N01)
A signal without an https link is not cited (US subcontracts, legacy rows); legacy Notion assessments are
not a source.
"""
from .level import classify
from .products import to_claims

CITED = {('grant', 'A'), ('tender_award', 'A'), ('tender_award', 'C'), ('contract', 'A')}


USASPENDING = 'https://www.usaspending.gov/recipient/{id}/latest'


def claims_from_signals(signals, identifiers=None):
    """identifiers: {scheme: value}. A US contract row without a link is cited through the recipient page
    on USAspending.gov when the recipient ID is known (the page lists the awards and the awarding agency)."""
    claims, cited = [], []
    rid = (identifiers or {}).get('USASPENDING_RECIPIENT')
    for s in signals:
        url = s.get('url') or ''
        if not url and rid and s.get('type') == 'contract' and s.get('layer') == 'A':
            s = {**s, 'url': USASPENDING.format(id=rid)}
            url = s['url']
        key = (s.get('type'), s.get('layer'))
        if key in CITED and url.startswith('https://'):
            cited.append(s)
            if s['layer'] == 'A':
                claims.append({'kind': 'contract', 'customer': True, 'documented': True, 'source': 'government',
                               'group': s['type'], 'named': True})
            else:
                claims.append({'kind': 'relevance', 'documented': True, 'source': 'government', 'group': s['type']})
        elif s.get('type') in ('association', 'exhibition'):
            claims.append({'kind': 'membership', 'source': 'self', 'group': s['type']})
        elif s.get('type') == 'self_declared':
            claims.append({'kind': 'capability_claim', 'source': 'self', 'group': 'self'})
    return claims, cited


def card(company, identity, signals, products=None, site=None, identifiers=None):
    """company: {id, name, country, website}; identity: registries result; products: products.extract result."""
    products = products or {'kept': [], 'rejected': [], 'provider': None}
    sig_claims, cited = claims_from_signals(signals, identifiers)
    claims = sig_claims + to_claims(products['kept'])
    matched = identity.get('decision') == 'matched'
    level = classify(claims, entity_resolved=matched)
    independent = bool(cited)
    honest_low = level['level'] in ('L0', 'L4')
    gaps = []
    if not matched:
        gaps.append('identity: ' + identity.get('decision', 'not_checked') + (f" ({identity['reason']})" if identity.get('reason') else ''))
    for url, why in (site or {}).get('skipped', []):
        gaps.append(f'website: {why}' + (f' ({url})' if url else ''))
    return {
        'company_id': company['id'], 'name': company['name'], 'country': company.get('country'),
        'identity': identity,
        'level': level['level'], 'confidence': level['confidence'], 'rule': level['rule'],
        'explanation': level['explanation'], 'ignored': level['ignored'], 'candidate_levels': level['candidate_levels'],
        'evidence': [{'type': s['type'], 'layer': s['layer'], 'counterpart': s.get('counterpart'), 'date': s.get('date'),
                      'url': s['url']} for s in cited],
        'products': products['kept'], 'products_rejected': len(products['rejected']), 'model': products.get('provider'),
        'pages_read': [p['url'] for p in (site or {}).get('pages', [])],
        'empty': {'technologies': 'not_assessed', 'financials': 'not_assessed', 'relationships': 'not_assessed',
                  'defence_revenue_share': 'unknown', 'products': 'assessed' if (site or {}).get('pages') else 'not_assessed'},
        'gaps': gaps,
        'gate': 'pass' if matched and (independent or honest_low) else 'fail',
    }


SIGNALS_SQL = """select s.company_id, s.type, s.layer, s.counterpart, s.signal_date::text as date, s.url
from companies.signal s where s.company_id = any(%(ids)s) and s.type <> 'legacy_assessment'
order by s.company_id, s.signal_date desc nulls last"""
