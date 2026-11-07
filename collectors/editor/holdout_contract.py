"""C3-10 offline, fail-closed validator for PRIVATE provisional evidence holdouts.

Validates internal references and literal-excerpt integrity only. Does NOT
independently verify a publisher, claim truth, model accuracy, or release gate.
Never outputs candidate text or private quote bodies.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from hashlib import sha256
import re

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_LABELS = ('supported', 'contradicted', 'insufficient')
_MAX_CASES = 500
_MAX_SPANS = 500


def audit_gold_candidate(holdout: Mapping) -> dict:
    """Audit private *source-entailment* labels; never certify a true gold set."""
    if not isinstance(holdout, Mapping) or holdout.get('schema') != 'bohun-c3-10-private-gold-candidate-v1':
        raise ValueError('invalid_holdout_schema')
    cases, spans = holdout.get('cases'), holdout.get('spans')
    if not isinstance(cases, list) or not 1 <= len(cases) <= _MAX_CASES:
        raise ValueError('invalid_holdout_cases')
    if not isinstance(spans, list) or not 1 <= len(spans) <= _MAX_SPANS:
        raise ValueError('invalid_holdout_spans')
    if holdout.get('publication_authorized') is not False or holdout.get('provider_calls') != 0:
        raise ValueError('holdout_has_unauthorized_activity')
    if holdout.get('source_class') != 'P2 only; 0 independent P1 attestations':
        raise ValueError('holdout_source_provenance_unverified')

    by_span = {}
    for span in spans:
        if not isinstance(span, Mapping):
            raise ValueError('invalid_holdout_span')
        ident, text = span.get('span_id'), span.get('text')
        if not isinstance(ident, str) or not ident or ident in by_span:
            raise ValueError('duplicate_or_invalid_span_id')
        if not isinstance(text, str) or not text.strip() or len(text) > 200:
            raise ValueError('invalid_span_excerpt')
        start, end = span.get('start_char_0'), span.get('end_char_exclusive')
        if type(start) is not int or type(end) is not int or start < 0 or end-start != len(text):
            raise ValueError('span_offsets_inconsistent')
        digest = span.get('text_sha256')
        if not isinstance(digest, str) or not _HEX.fullmatch(digest) or sha256(text.encode('utf-8')).hexdigest() != digest:
            raise ValueError('span_integrity_mismatch')
        if (span.get('independent_retrieval_attested') is not False
                or span.get('source_class') != 'P2'):
            raise ValueError('unverified_source_trust_escalation')
        if not isinstance(span.get('material_id'), str) or not span.get('material_id').startswith('syn-'):
            raise ValueError('invalid_span_material')
        if not isinstance(span.get('url'), str) or not span['url'].startswith('https://'):
            raise ValueError('invalid_span_url')
        by_span[ident] = span

    seen_cases, counts, types, materials = set(), Counter(), Counter(), set()
    for case in cases:
        if not isinstance(case, Mapping):
            raise ValueError('invalid_holdout_case')
        ident, material, claim = case.get('case_id'), case.get('material_id'), case.get('claim_text')
        if not isinstance(ident, str) or not ident or ident in seen_cases:
            raise ValueError('duplicate_or_invalid_case_id')
        seen_cases.add(ident)
        if not isinstance(material, str) or not material.startswith('syn-'):
            raise ValueError('invalid_case_material')
        if not isinstance(claim, str) or not claim.strip() or len(claim) > 3000:
            raise ValueError('invalid_claim_text')
        digest = case.get('claim_sha256')
        if not isinstance(digest, str) or not _HEX.fullmatch(digest) or digest != sha256(claim.encode()).hexdigest():
            raise ValueError('claim_integrity_mismatch')
        label = case.get('expected_source_entailment_label')
        if label not in _LABELS:
            raise ValueError('invalid_entailment_label')
        if case.get('independent_second_annotation_complete') is not False:
            raise ValueError('unverified_second_annotation')
        refs = case.get('quoted_span_ids')
        if not isinstance(refs, list) or not refs or len(refs) != len(set(refs)):
            raise ValueError('invalid_case_span_references')
        for ref in refs:
            if not isinstance(ref, str) or ref not in by_span or by_span[ref]['material_id'] != material:
                raise ValueError('case_span_cross_material_or_missing')
        counts[label] += 1
        types[str(case.get('origin', 'unclassified'))] += 1
        materials.add(material)

    selected = holdout.get('selected_material_ids')
    if (not isinstance(selected, list) or len(selected) != len(set(selected))
            or any(type(s) is not int or 'syn-'+str(s) not in materials for s in selected)
            or len(materials) != len(selected)):
        raise ValueError('selected_material_ids_mismatch')
    return {
        'schema': 'bohun-c3-10-gold-candidate-audit-v1',
        'status': 'provisional_source_entailment_only',
        'cases_count': len(cases),
        'spans_count': len(spans),
        'material_count': len(materials),
        'counts': {label: counts[label] for label in _LABELS},
        'synthetic_mutation_count': types['synthetic_mutation'],
        'independent_second_annotation_complete': False,
        'real_world_truth_attested': False,
        'independent_retrieval_attested': False,
        'publication_authorized': False,
        'offline_only': True,
    }
