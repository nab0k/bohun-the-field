"""Defence participation level L0-L5 by the owner's methodology (rules R01-R07, limits N01-N10).

B2CI-03, design B v1 (BOHUN-local EXP-033). Pure functions, no network, no model: the model only
extracts claims with quotes; this code decides the level. Expected answers live in
tests/companies/fixtures/level_*.yaml and were fixed before this code was written.

Input: a list of claims (dicts). Fields used:
  kind        product | component | service | contract | award | bid | notice | relevance |
              capability_claim | marketing | membership | regulatory_flag | division_mention |
              affiliate | end_of_engagement | identity_conflict | same_brand_entities |
              generic_material | non_defence_project
  specialized True when the item is specifically military (product, component or service)
  named       True when the product, programme or contract is named in the source
  documented  True when the source states the fact itself (not a capability or intention)
  customer    True when a defence customer is documented
  source      'self' (the company), 'second_party' (partner, customer, former owner),
              'government' (ministry, agency, contract register), 'media', 'registry'
  group       independence group of the source (reprints of one release share one group)
  historical  True when the engagement has ended (R07)
  service_kind ordinary service type for L5 (logistics, recruitment, cleaning, finance)
Entity-level options: entity_resolved (default True) — the legal entity is matched in a register.
"""
from __future__ import annotations

LEVEL_ORDER = {'L1': 1, 'L2': 2, 'L3': 3, 'L5': 5, 'L4': 4, 'L0': 9}
CONF_ORDER = ['verified', 'supported', 'candidate', 'disputed', 'unknown']
CANDIDATE_ONLY = {'marketing', 'membership', 'regulatory_flag', 'capability_claim', 'non_defence_project'}


def _independent(c):
    return c.get('source') in ('second_party', 'government', 'media')


def _strength(claims, entity_resolved):
    """verified / supported for a set of claims that support one level."""
    groups = {c.get('group') or id(c) for c in claims if _independent(c)}
    if any(c.get('source') == 'government' for c in claims) and any(c.get('named', True) for c in claims):
        return 'verified'
    named = any(c.get('named') for c in claims)
    if not named:
        return 'supported'
    if len(groups) >= 2:
        return 'verified'
    if len(groups) == 1 and entity_resolved and any(c.get('source') == 'second_party' for c in claims):
        return 'verified'
    return 'supported'


def classify(claims, entity_resolved=True):
    out = {'level': 'L0', 'confidence': 'unknown', 'rule': None, 'explanation': '', 'flags': [],
           'candidate_levels': [], 'manual_review': False, 'time_class': 'current', 'event_stage': None,
           'ignored': [], 'independence_group_count': 0}
    groups = {c.get('group') for c in claims if c.get('group')}
    out['independence_group_count'] = len(groups)

    usable = []
    for c in claims:
        k = c.get('kind')
        if k == 'affiliate':
            out['ignored'].append('N05: affiliate or owner level is not inherited')
        elif k == 'identity_conflict':
            out['manual_review'] = True
            out['flags'].append('no_automatic_merge')
            out['confidence'] = 'disputed'
        elif k == 'same_brand_entities':
            out['flags'].append('distinct_legal_entities')
        elif k == 'regulatory_flag':
            out['flags'].append('regulatory_flag')
        elif k == 'membership':
            out['ignored'].append('N02: membership or exhibition is a lead, not a level')
        elif k == 'marketing':
            out['ignored'].append('N01: marketing wording does not give a level')
        elif k == 'non_defence_project':
            out['flags'].append(c.get('flag', 'non_military'))
        elif k == 'division_mention':
            out['candidate_levels'] = ['L1', 'L2']
            out['manual_review'] = True
            out['confidence'] = 'candidate'
            out['rule'] = 'R06'
        elif k == 'bid':
            out['event_stage'] = 'bid'
            out['confidence'] = 'candidate'
            out['rule'] = 'N04'
        elif k == 'notice':
            out['event_stage'] = 'announcement'
            out['rule'] = 'N04'
        elif k == 'end_of_engagement':
            out['time_class'] = 'historical'
            out['rule'] = out['rule'] or 'R07'
        else:
            usable.append(c)
        if c.get('historical'):
            out['time_class'] = 'historical'

    def pick(level, rule, items, why):
        if LEVEL_ORDER[level] < LEVEL_ORDER[out['level']] or out['level'] == 'L0':
            out.update(level=level, rule=rule, explanation=why,
                       confidence=_strength(items, entity_resolved))

    for c in usable:
        k, spec, doc = c.get('kind'), c.get('specialized'), c.get('documented', True)
        self_only_unnamed = c.get('source') == 'self' and not c.get('named')
        if k in ('product', 'service') and spec and doc and not self_only_unnamed:
            pick('L1', 'R01', [x for x in usable if x.get('kind') in ('product', 'service') and x.get('specialized')],
                 'confirmed specialized military product or service')
        elif k == 'component' and spec and doc and not self_only_unnamed:
            pick('L2', 'R02', [x for x in usable if x.get('kind') == 'component' and x.get('specialized')],
                 'confirmed specialized component or programme role')
        elif k in ('product', 'service', 'component') and spec and self_only_unnamed:
            if 'L2' not in out['candidate_levels']:
                out['candidate_levels'].append('L2' if k == 'component' else 'L1')
        elif k in ('contract', 'award') and c.get('customer') and doc:
            if c.get('service_kind'):
                pick('L5', 'R05', [c], 'ordinary service for a defence customer')
            else:
                pick('L3', 'R03', [x for x in usable if x.get('kind') in ('contract', 'award')],
                     'documented defence contract for general product, service or unstated role')
                if k == 'award' and not c.get('executed'):
                    out['event_stage'] = 'awarded'
        elif k == 'relevance' and doc:
            pick('L4', 'R04', [c], 'documented relevant competence without military sale')
        elif k == 'capability_claim':
            if out['level'] == 'L0':
                out.update(level='L4', rule='R04', confidence='candidate',
                           explanation='self-stated relevance only (N01): candidate')
        elif k == 'generic_material':
            pass

    if out['level'] == 'L0' and out['rule'] is None:
        out['explanation'] = 'no defence link established (not the same as "no link")'
    if 'no_automatic_merge' in out['flags']:
        out['confidence'] = 'disputed'
    if out['level'] in ('L1', 'L2', 'L3') and out['confidence'] == 'unknown':
        out['confidence'] = 'supported'
    return out


def revenue_share(numerator, denominator):
    """Defence revenue share only for comparable period, currency and scope (methodology 10.3)."""
    if not numerator or not denominator:
        return {'share': 'unknown', 'reason': 'missing value'}
    for key in ('year', 'currency', 'scope'):
        if numerator.get(key) != denominator.get(key):
            return {'share': 'unknown', 'reason': f'different {key}', 'inputs': [numerator, denominator]}
    if not denominator.get('amount'):
        return {'share': 'unknown', 'reason': 'zero or missing denominator', 'inputs': [numerator, denominator]}
    return {'share': round(numerator['amount'] / denominator['amount'], 4), 'reason': 'comparable'}
