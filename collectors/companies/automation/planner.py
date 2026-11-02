"""Deterministic keyset planning, independent of IO and total registry size."""
from dataclasses import dataclass
from datetime import timedelta

from .contracts import (CompanySnapshot, INTERVAL_DAYS, JobSpec, Policy, SECTIONS,
                        SectionState, aware, digest, require)


@dataclass(frozen=True)
class PlanPage:
    jobs: tuple
    checkpoint: str | None
    has_more: bool
    companies_considered: int
    sections_accounted: int
    already_planned: int
    not_due: int


def plan_page(companies, *, policy, as_of, after=None, limit=100, existing_keys=frozenset(),
              section_states=None, section_inputs=None):
    """Input is a stable ID-ordered snapshot (or keyset page plus lookahead).

    The durable adapter must persist jobs and checkpoint in one transaction and
    do a subsequent sweep for IDs inserted behind the cursor. No live DB here.
    """
    require(isinstance(policy, Policy), "POLICY_REQUIRED")
    now = aware(as_of)
    require(type(limit) is int and 1 <= limit <= 1000, "PAGE_LIMIT_INVALID")
    records = tuple(companies)
    require(all(isinstance(r, CompanySnapshot) for r in records), "SNAPSHOT_REQUIRED")
    ids = [r.company_id for r in records]
    require(ids == sorted(set(ids)), "STRICT_ID_ORDER_REQUIRED")
    remaining = [r for r in records if after is None or r.company_id > after]
    page = remaining[:limit]
    jobs, already, not_due = [], 0, 0
    states = section_states or {}
    inputs = section_inputs or {}
    for company in page:
        for section in SECTIONS:
            prior = states.get((company.company_id, section), SectionState())
            require(isinstance(prior, SectionState), "SECTION_STATE_REQUIRED")
            input_hash = inputs.get((company.company_id, section), company.version)
            if prior.next_due is not None and prior.input_hash == input_hash and aware(prior.next_due) > now:
                not_due += 1
                continue
            state, reason = "READY", None
            if company.status == "excluded":
                state, reason = "CANCELLED", "RECORD_EXCLUDED"
            elif company.manual_hold:
                state, reason = "NEEDS_REVIEW", "MANUAL_HOLD"
            elif not company.identity_ready and section not in ("IDENTITY", "SOURCES"):
                state, reason = "NEEDS_REVIEW", "IDENTITY_PENDING"
            seconds = INTERVAL_DAYS[section] * 86400
            revision = str(int(now.timestamp()) // seconds)
            job = JobSpec("ENRICH_SECTION", policy.fingerprint, policy.version, input_hash,
                          company.company_id, section, revision=revision, initial_state=state, reason=reason)
            if job.job_key in existing_keys:
                already += 1
            else:
                jobs.append(job)
    return PlanPage(tuple(jobs), page[-1].company_id if page else after, len(remaining) > limit,
                    len(page), len(page) * len(SECTIONS), already, not_due)


def next_due(section, checked_at):
    require(section in SECTIONS, "SECTION_REQUIRED")
    return aware(checked_at) + timedelta(days=INTERVAL_DAYS[section])


def discovery_job(*, source_ref, cursor, source_version, as_of, policy, disabled=False,
                  existing_keys=frozenset(), interval_seconds=3600):
    """One task per configured due window/input version; durable cursor is a later port."""
    require(type(interval_seconds) is int and 60 <= interval_seconds <= 30 * 86400, "SOURCE_INTERVAL_INVALID")
    window = str(int(aware(as_of).timestamp()) // interval_seconds)
    require(isinstance(policy, Policy) and type(disabled) is bool, "SOURCE_POLICY_REQUIRED")
    job = JobSpec("DISCOVER_SOURCE", policy.fingerprint, policy.version,
                  digest([cursor, source_version]), source_ref=source_ref, revision=window,
                  initial_state="CANCELLED" if disabled else "READY",
                  reason="SOURCE_DISABLED" if disabled else None)
    return None if job.job_key in existing_keys else job
