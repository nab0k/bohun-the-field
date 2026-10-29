"""Evidence admission. Literal alignment alone never establishes semantics."""
from dataclasses import asdict
from contracts import ChangeProposal, EvidenceObservation, SECTIONS, SOURCE_TYPES, digest
from baseline import identifier_key
from identity import issuer_bound
from planner import FIELDS
from sources import json_field


def observe(subject, claim, source, rec):
    return EvidenceObservation(subject=subject, section=claim['section'], field=claim['field'],
                               value=claim['value'], source_type=claim['source_type'],
                               receipt_key=rec.key, locator=claim['locator'],
                               subject_identifiers=tuple(claim.get('subject_identifiers', [])),
                               modality=claim.get('modality', 'asserted'), units=claim.get('units'),
                               currency=claim.get('currency'), period=claim.get('period'))


def validate(obs, source, rec, row, trusted_bindings):
    reasons = []
    if obs.section not in SECTIONS or obs.field not in FIELDS.get(obs.section, ()):
        reasons.append('FIELD_NOT_IN_CANVAS')
    if obs.receipt_key != rec.key:
        reasons.append('RECEIPT_MISMATCH')
    if obs.source_type not in SOURCE_TYPES or obs.source_type != source['source_type']:
        reasons.append('SOURCE_TYPE_MISMATCH')
    if obs.source_type == 'inference_candidate':
        reasons.append('INFERENCE_IS_NOT_FACT')
    if not issuer_bound(source['source_id'], obs.subject_identifiers, trusted_bindings):
        reasons.append('ISSUER_NOT_BOUND')
    ids = {identifier_key(i) for i in obs.subject_identifiers}
    row_ids = {identifier_key(i) for i in row.get('identifiers', [])}
    if not ids or not ids <= row_ids:
        reasons.append('WRONG_SUBJECT')
    try:
        loc = obs.locator
        if source['format'] == 'json':
            if json_field(source['raw'], loc['path']) != obs.value:
                reasons.append('VALUE_NOT_AT_PATH')
            raw_ids = {identifier_key(i) for i in source['raw'].get('identifiers', [])}
            if not ids <= raw_ids:
                reasons.append('SOURCE_SUBJECT_MISMATCH')
            qualifiers = source['raw'].get('qualifiers', {}).get(obs.section, {}).get(obs.field, {})
            if obs.modality != qualifiers.get('modality', 'asserted'):
                reasons.append('MODALITY_NOT_BOUND_TO_SOURCE')
            for name in ('units', 'currency', 'period'):
                if getattr(obs, name) != qualifiers.get(name):
                    reasons.append('FINANCIAL_QUALIFIER_NOT_BOUND')
        elif source['format'] in {'text', 'html'}:
            text = source.get('text', '')
            a, b, quote = loc['start'], loc['end'], loc['quote']
            if (type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text)
                    or text[a:b] != quote):
                reasons.append('NON_LITERAL_SPAN')
            # No untested general subject/negation classifier disguised as science.
            reasons.append('SEMANTIC_REVIEW_REQUIRED')
        else:
            reasons.append('NOT_PARSED')
    except (KeyError, TypeError, ValueError):
        reasons.append('INVALID_LOCATOR')
    if obs.modality not in {'asserted', 'announced', 'planned', 'negative', 'completed'}:
        reasons.append('UNKNOWN_MODALITY')
    if obs.section == 'programmes_contracts' and obs.field == 'contract' and obs.source_type == 'official_programme_record':
        reasons.append('PROGRAMME_NOT_DELIVERY_CONTRACT')
    if obs.section == 'relationships' and obs.field != 'accounting_parent':
        reasons.append('RELATION_TYPE_UNSUPPORTED')
    if obs.section == 'financials' and not all((obs.units, obs.currency, obs.period)):
        reasons.append('FINANCIAL_CONTEXT_REQUIRED')
    if obs.section == 'classification':
        # Native taxonomy reuse is tested separately. No guessed category/PRIME/Tier.
        reasons.append('TAXONOMY_REVIEW_REQUIRED')
    if obs.value in (None, '', [], {}):
        reasons.append('EMPTY_DOES_NOT_CLEAR')
    return tuple(dict.fromkeys(reasons))


def proposal(obs, baseline_row, current_row, reasons=()):
    section, field = obs.section, obs.field
    old = baseline_row.get('fields', {}).get(section, {}).get(field)
    current = current_row.get('fields', {}).get(section, {}).get(field)
    hold = list(reasons)
    if current_row.get('qa_status') in {'HOLD', 'REJECT'} or current_row.get('excluded'):
        hold.append('RECORD_GATE')
    if section + '.' + field in current_row.get('blocked_fields', []):
        hold.append('FIELD_GATE')
    if current != old:
        hold.append('CONCURRENT_OR_MANUAL_EDIT')
    if current == obs.value:
        operation = 'unchanged'
    elif obs.value in (None, '', [], {}):
        operation = 'clear'
        hold.append('CLEAR_REQUIRES_REVIEW')
    elif current not in (None, '', [], {}):
        operation = 'update'
        hold.append('NONEMPTY_REPLACEMENT')
    else:
        operation = 'fill'
    return ChangeProposal(obs.subject, section, field, old, current, obs.value,
                          digest(current), operation, (obs.key,),
                          'HOLD' if hold else 'UNCHANGED' if operation == 'unchanged' else 'ACCEPTED_PRIVATE',
                          tuple(dict.fromkeys(hold)))


def private_projection(row):
    """Report copy, not a public projector. Minimise UA geodata even here."""
    import copy
    result = copy.deepcopy(row)
    if row.get('fields', {}).get('geography', {}).get('country') == 'UA':
        result['fields']['geography'] = {'country': 'UA'}
    return result
