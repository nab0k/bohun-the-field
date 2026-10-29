"""Immutable input snapshot; freshness and coverage are explicit, never inferred."""
import copy
import json
from pathlib import Path
from contracts import digest, iso

COVERAGE = ('registry', 'notion', 'aliases', 'identifiers')


def identifier_key(i):
    if not all(isinstance(i.get(k), str) and i[k].strip() for k in ('scheme', 'jurisdiction', 'value')):
        raise ValueError('IDENTIFIER_SCHEME_JURISDICTION_REQUIRED')
    return i['scheme'], i['jurisdiction'], i['value']


class Baseline:
    def __init__(self, data):
        self._data = copy.deepcopy(data)
        self.hash = digest(self._data)
        self.completeness = dict(data['completeness'])
        self.checked_at = dict(data['checked_at'])
        for key in COVERAGE:
            if type(self.completeness.get(key)) is not bool:
                raise ValueError('COVERAGE_REQUIRED')
            iso(self.checked_at[key])
        self.rows = {r['id']: copy.deepcopy(r) for r in data['companies']}
        if len(self.rows) != len(data['companies']):
            raise ValueError('DUPLICATE_BASELINE_ID')
        for row in self.rows.values():
            for i in row.get('identifiers', []):
                identifier_key(i)

    @property
    def complete(self):
        return all(self.completeness[k] for k in COVERAGE)

    def row(self, key):
        return copy.deepcopy(self.rows[key])

    def as_dict(self):
        return copy.deepcopy(self._data)


def read_saved(path, expected_hash):
    data = json.loads(Path(path).read_text())
    if digest(data) != expected_hash:
        raise ValueError('BASELINE_HASH_MISMATCH')
    return Baseline(data)
