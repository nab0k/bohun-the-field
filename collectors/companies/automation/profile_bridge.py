"""Read-only profile assessment and atomic-commit port for the EXP016 planner.

No HTTP, SQL, credentials or second queue. A backend must supply complete current
reads and atomically commit jobs with trigger/profile checkpoints. Saved Notion
exports can be assessed, but must not be presented as current backend reads.
"""
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Protocol
from .event_triggers import core, plan_triggers, trigger_records
FIELDS = {'IDENTITY': ('legal_name', 'registration_id'), 'CLASSIFICATION': ('category',), 'PRODUCTS': ('product',), 'TECHNOLOGIES': ('technology',), 'PROGRAMMES': ('participation', 'contract'), 'EVENTS': ('event',), 'RELATIONSHIPS': ('accounting_parent',), 'FINANCIALS': ('revenue',), 'GEOGRAPHY': ('country', 'city'), 'SOURCES': ('reference',)}
SCALARS = {('IDENTITY', 'legal_name'): 'Legal name', ('IDENTITY', 'registration_id'): 'Legal ID', ('CLASSIFICATION', 'category'): 'Product', ('RELATIONSHIPS', 'accounting_parent'): 'Parent', ('GEOGRAPHY', 'country'): 'Country', ('GEOGRAPHY', 'city'): 'City', ('SOURCES', 'reference'): 'Source URL'}
RELATED = {('PRODUCTS', 'product'): 'Site - Products', ('TECHNOLOGIES', 'technology'): 'B - Технологии', ('PROGRAMMES', 'participation'): 'Site - Programmes', ('PROGRAMMES', 'contract'): 'Site - Programmes', ('EVENTS', 'event'): 'Site - Developments', ('RELATIONSHIPS', 'accounting_parent'): 'Site - Relationships', ('FINANCIALS', 'revenue'): 'Overview - Revenue'}

@dataclass(frozen=True)
class CardRead:
    company: core.CompanySnapshot
    properties_json: str
    read_at: object
    complete: bool

    def __post_init__(self):
        core.require(isinstance(self.company, core.CompanySnapshot), 'COMPANY_REQUIRED')
        props = core.parse_json(self.properties_json)
        core.require(isinstance(props, dict), 'PROPERTIES_REQUIRED')
        core.aware(self.read_at)
        core.require(type(self.complete) is bool, 'COMPLETENESS_REQUIRED')

def assess_card(card):
    """Assess presence, not truth, sufficiency of evidence or publication status."""
    props = core.parse_json(card.properties_json)
    sections = {}
    for section in core.SECTIONS:
        fields, missing, unread, omissions = ({}, [], [], [])
        for field in FIELDS[section]:
            if section == 'GEOGRAPHY' and field == 'city' and (props.get('Country') == 'UA'):
                omissions.append('city:UA_PRECISE_LOCATION_OMITTED')
                continue
            key = (section, field)
            value = props.get(SCALARS.get(key, ''))
            if isinstance(value, str):
                value = value.strip()
            if value not in (None, '', [], {}):
                core.require(isinstance(value, str), 'SCALAR_PROPERTY_INVALID')
                fields[field] = value
            else:
                related = props.get(RELATED.get(key, ''))
                if related not in (None, '', [], {}):
                    core.require(isinstance(related, list) and all((isinstance(x, str) for x in related)), 'RELATION_PROPERTY_INVALID')
                    unread.append(field)
                    fields[field] = {'unread_leads': sorted(set(related))}
                else:
                    missing.append(field)
                    fields[field] = None
        sections[section] = {'present_fields': fields, 'missing_fields': missing, 'unread_fields': unread, 'omissions': omissions, 'profile_hash': core.digest(fields)}
    return sections

def prepare_batch(triggers, cards, checkpoint, *, policy, as_of, max_age=timedelta(minutes=5)):
    """Checkpoint maps use (company_id, section) keys, like the original planner.

    This is an in-process port; durable encoding/transactions belong to EXP016.
    Never treat missing field presence as a completed, unsuccessful search.
    """
    triggers = tuple(triggers)
    now = core.aware(as_of)
    core.require(max_age > timedelta(0) and max_age <= timedelta(minutes=5), 'MAX_AGE_INVALID')
    card_map = {c.company.company_id: c for c in cards}
    core.require(len(card_map) == len(cards), 'DUPLICATE_CARD')
    wanted = {t.company_id for t in triggers}
    core.require(wanted == set(card_map), 'CURRENT_CARD_SET_MISMATCH')
    inputs = dict(checkpoint.get('section_inputs', {}))
    hashes = dict(checkpoint.get('profile_hashes', {}))
    states = dict(checkpoint.get('section_states', {}))
    assessments = {}
    for cid, card in card_map.items():
        core.require(card.complete, 'PARTIAL_CARD_READ')
        core.require(timedelta(0) <= now - core.aware(card.read_at) <= max_age, 'STALE_CARD_READ')
        assessments[cid] = assess_card(card)
        for section, report in assessments[cid].items():
            key = (cid, section)
            h = report['profile_hash']
            if hashes.get(key) != h:
                inputs[key] = core.digest([inputs.get(key), 'profile', h])
                hashes[key] = h
            prior = states.get(key)
            if prior and prior.coverage == 'HAS_APPROVED_CLAIMS' and (report['missing_fields'] or report['unread_fields']):
                states[key] = replace(prior, next_due=None)
    planned = plan_triggers(triggers, tuple((c.company for c in cards)), policy=policy, as_of=now, section_states=states, section_inputs=inputs, existing_keys=checkpoint.get('existing_keys', frozenset()), seen_triggers=checkpoint.get('seen_triggers', frozenset()))
    known_keys = set(checkpoint.get('existing_keys', ()))
    known_keys.update((j.job_key for j in planned['jobs']))
    return {**planned, 'profile_hashes': hashes, 'assessments': assessments, 'existing_keys': frozenset(known_keys), 'section_states': dict(checkpoint.get('section_states', {})), 'trigger_records': trigger_records(triggers)}

class Backend(Protocol):

    def read_current(self, company_ids):
        """Return (revision, tuple[CardRead], complete checkpoint), same snapshot.

        Canonical IDs must already be admitted; include manual holds and exclude
        status from authoritative storage. Partial pagination must fail closed.
        """

    def commit_batch(self, expected_revision, prepared):
        """Atomically compare source/card/checkpoint revisions and persist all
        jobs, cumulative existing_keys, profile hashes, inputs and seen keys.
        Return APPLIED or CONFLICT. Never replace the job-key set with only the
        current batch's new jobs (an empty repeat must not erase old keys).
        Check trigger_records against original Trigger.key before observations.
        section_states is unchanged read metadata, never permission to update g4.
        Durable batch key/receipt and lookup after unknown belong to the shared
        backend; it must not blindly issue a second commit.
        """

class SharedBackendLink:
    """Thin A26 boundary into the original EXP016 PostgresBackend.

    Pass the reviewed shared module; no private-path production imports or new
    storage. Trigger descriptors go to its existing constructor, while the
    commit projection matches its strict prepared schema. RPC is injected.
    """
    PREPARED_FIELDS = frozenset({'jobs', 'existing_keys', 'seen_triggers', 'section_inputs', 'profile_hashes', 'unresolved_companies', 'checks', 'external_calls', 'persistent_write_performed', 'assessments'})

    def __init__(self, shared, rpc, *, triggers, policy, clock):
        self.records = trigger_records(tuple(triggers))
        receipts = tuple((shared.trigger_receipt(r['company_id'], r['origin_id'], r['content_hash'], tuple(r['affected_sections'])) for r in self.records))
        core.require(all((a['key'] == b['key'] for a, b in zip(self.records, receipts))), 'TRIGGER_KEY_CONTRACT_CHANGED')
        self.delegate = shared.PostgresBackend(rpc, policy=policy, clock=clock, card_factory=CardRead, triggers=receipts)
        self.reviews = {}

    def read_current(self, company_ids):
        revision, cards, checkpoint = self.delegate.read_current(company_ids)
        self.reviews[revision] = dict(checkpoint['section_states'])
        return (revision, cards, checkpoint)

    def commit_batch(self, expected_revision, prepared):
        core.require(prepared['trigger_records'] == self.records, 'TRIGGER_BATCH_CHANGED')
        core.require(expected_revision in self.reviews and prepared['section_states'] == self.reviews[expected_revision], 'REVIEW_METADATA_CHANGED')
        return self.delegate.commit_batch(expected_revision, {k: prepared[k] for k in self.PREPARED_FIELDS})

    @property
    def last_batch(self):
        return self.delegate.last_batch

def dispatch(triggers, backend: Backend, *, policy, as_of):
    triggers = tuple(triggers)
    if not triggers:
        return {'status': 'NO_CHANGE', 'jobs': 0}
    try:
        revision, cards, checkpoint = backend.read_current(tuple(sorted({t.company_id for t in triggers})))
        # read_at is stamped by the database, so a live clock must be read after it.
        prepared = prepare_batch(triggers, cards, checkpoint, policy=policy, as_of=as_of() if callable(as_of) else as_of)
    except core.ContractError as exc:
        return {'status': 'HOLD', 'reason': str(exc)}
    except Exception:
        return {'status': 'HOLD', 'reason': 'CURRENT_READ_FAILED'}
    try:
        result = backend.commit_batch(revision, prepared)
    except Exception:
        return {'status': 'UNKNOWN', 'reason': 'COMMIT_RECONCILIATION_REQUIRED'}
    if result == 'CONFLICT':
        return {'status': 'HOLD', 'reason': 'CURRENT_VERSION_CHANGED'}
    if result != 'APPLIED':
        return {'status': 'UNKNOWN', 'reason': 'COMMIT_RECONCILIATION_REQUIRED'}
    return {'status': 'APPLIED', 'jobs': len(prepared['jobs']), 'checks': prepared['checks'], 'assessments': prepared['assessments']}
