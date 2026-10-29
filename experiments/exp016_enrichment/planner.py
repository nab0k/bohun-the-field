"""Two queues, per-field needs and explicit unknowns; no network scheduler."""
from contracts import ResearchTask, SECTIONS

FIELDS = {
    'identity': ('legal_name', 'registration_id'),
    'classification': ('category',), 'products': ('product',),
    'technologies': ('technology',), 'programmes_contracts': ('participation', 'contract'),
    'events': ('event',), 'relationships': ('accounting_parent',),
    'financials': ('revenue',), 'geography': ('country', 'city'), 'sources': ('reference',),
}


def tasks(row, source_allowances, *, stale=(), conflicts=(), queue='existing'):
    result = []
    for section in SECTIONS:
        for field in FIELDS[section]:
            key = section + '.' + field
            value = row.get('fields', {}).get(section, {}).get(field)
            reason = ('conflict' if key in conflicts else 'stale' if key in stale
                      else 'missing' if value in (None, '', [], {}) else None)
            if reason:
                result.append(ResearchTask(row['id'], section, field, reason,
                                           tuple(source_allowances), queue))
    return result


def queues(baseline, fixed_existing, candidates, allowances):
    if any(k not in baseline.rows for k in fixed_existing):
        raise ValueError('FIXED_COHORT_MISSING')
    existing = [t for k in fixed_existing for t in tasks(baseline.row(k), allowances)]
    discovery = [ResearchTask(c['candidate_id'], 'identity', 'registration_id',
                             'discovery', tuple(allowances), 'discovery') for c in candidates]
    return existing, discovery
