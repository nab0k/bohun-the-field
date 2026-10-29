"""Deterministic admission policy, not a substitute for Nomenklatura scoring."""
from baseline import identifier_key


def resolve(candidate, baseline):
    ids = {identifier_key(i) for i in candidate.get('identifiers', [])}
    if not ids:
        return {'status': 'SUGGESTION', 'subject': None, 'reason': 'NO_STRONG_ID'}
    matches = []
    for row in baseline.rows.values():
        old = {identifier_key(i) for i in row.get('identifiers', [])}
        if not ids & old:
            continue
        if candidate.get('entity_kind') != row.get('entity_kind'):
            return {'status': 'HOLD', 'subject': row['id'], 'reason': 'GROUP_SUBSIDIARY'}
        if any(a[:2] == b[:2] and a[2] != b[2] for a in ids for b in old):
            return {'status': 'HOLD', 'subject': row['id'], 'reason': 'IDENTIFIER_CONFLICT'}
        matches.append(row['id'])
    if len(matches) > 1:
        return {'status': 'HOLD', 'subject': None, 'reason': 'AMBIGUOUS_STRONG_ID'}
    if matches:
        return {'status': 'BOUND', 'subject': matches[0], 'reason': 'EXACT_SCOPED_IDENTIFIER'}
    if not baseline.complete:
        return {'status': 'UNKNOWN_NOVELTY', 'subject': None, 'reason': 'INCOMPLETE_UNION'}
    # Only candidate status; never a canonical/public company insertion.
    return {'status': 'NEW_CANDIDATE', 'subject': None, 'reason': 'ABSENT_FROM_COMPLETE_UNION'}


def issuer_bound(source_id, identifiers, trusted_bindings):
    ids = {identifier_key(i) for i in identifiers}
    allowed = {identifier_key(i) for i in trusted_bindings.get(source_id, [])}
    return bool(ids and ids <= allowed)
