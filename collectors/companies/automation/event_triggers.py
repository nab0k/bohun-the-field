"""A26 → existing EXP016 planner. Offline adapter, not another queue or scheduler."""
from collections import defaultdict
from dataclasses import dataclass
from . import contracts as core, planner

@dataclass(frozen=True)
class Trigger:
    company_id: str
    origin_id: str
    content_hash: str
    affected_sections: tuple = ('EVENTS',)

    def __post_init__(self):
        core.identifier(self.company_id)
        core.identifier(self.origin_id)
        core.require(core.is_hash(self.content_hash), 'TRIGGER_HASH_REQUIRED')
        core.require(type(self.affected_sections) is tuple and bool(self.affected_sections) and all((s in core.SECTIONS for s in self.affected_sections)), 'SECTION_REQUIRED')

    @property
    def key(self):
        return core.digest([self.company_id, self.origin_id, self.content_hash, sorted(set(self.affected_sections))])

def trigger_records(triggers):
    """Canonical descriptors for the shared observation writer; no new codec.

    Existing Trigger.key remains authoritative. This copies source metadata,
    never an inferred company identity or a newly admitted factual claim.
    """
    records = {}
    for t in triggers:
        core.require(isinstance(t, Trigger), 'TYPED_TRIGGER_REQUIRED')
        records[t.key] = {'key': t.key, 'company_id': t.company_id, 'origin_id': t.origin_id, 'content_hash': t.content_hash, 'affected_sections': sorted(set(t.affected_sections))}
    return tuple((records[key] for key in sorted(records)))

def plan_triggers(triggers, companies, *, policy, as_of, section_states=None, section_inputs=None, existing_keys=frozenset(), seen_triggers=frozenset()):
    """Check all ten sections once per known triggered company in this batch.

    The shared durable adapter must persist seen keys, inputs and new jobs
    atomically. Within-batch grouping and deterministic job keys are tested;
    cross-worker leases/coalescing are not implemented by this pure function.
    """
    if len({c.company_id for c in companies}) != len(companies):
        raise ValueError('DUPLICATE_COMPANY_ID')
    by_id = {c.company_id: c for c in companies}
    grouped = defaultdict(dict)
    for t in triggers:
        if not isinstance(t, Trigger):
            raise ValueError('TYPED_TRIGGER_REQUIRED')
        grouped[t.company_id][t.key] = t
    states = section_states or {}
    inputs = dict(section_inputs or {})
    job_keys = set(existing_keys)
    jobs, seen, unknown, reports = ([], set(seen_triggers), [], [])
    for cid in sorted(grouped):
        if cid not in by_id:
            unknown.append(cid)
            continue
        company = by_id[cid]
        fresh = [t for key, t in grouped[cid].items() if key not in seen]
        changes = defaultdict(list)
        for t in fresh:
            for s in set(t.affected_sections):
                changes[s].append(t.key)
        for section, keys in changes.items():
            old = inputs.get((cid, section), company.version)
            inputs[cid, section] = core.digest([old, sorted(keys)])
        page = planner.plan_page((company,), policy=policy, as_of=as_of, limit=1, existing_keys=job_keys, section_states=states, section_inputs=inputs)
        jobs.extend(page.jobs)
        job_keys.update((j.job_key for j in page.jobs))
        seen.update(grouped[cid])
        reports.append({'company_id': cid, 'sections_checked': page.sections_accounted, 'trigger_versions': len(grouped[cid]), 'new_trigger_versions': len(fresh), 'new_section_jobs': len(page.jobs), 'already_planned': page.already_planned, 'not_due': page.not_due})
    return {'jobs': tuple(jobs), 'seen_triggers': frozenset(seen), 'section_inputs': inputs, 'unresolved_companies': tuple(unknown), 'checks': tuple(reports), 'external_calls': 0, 'persistent_write_performed': False}
