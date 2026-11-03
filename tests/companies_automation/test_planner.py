from datetime import datetime, timedelta, timezone
from dataclasses import replace
import unittest

from collectors.companies.automation.contracts import (CompanySnapshot, ContractError, Policy,
                                                      SECTIONS, SectionState, digest)
from collectors.companies.automation.planner import plan_page, next_due, discovery_job

NOW = datetime(2026, 11, 1, 12, tzinfo=timezone.utc)
POLICY = Policy()


def records(n):
    return tuple(CompanySnapshot(f"synthetic_{i:05d}", digest(["input", i])) for i in range(n))


class PlannerTests(unittest.TestCase):
    def test_3751_companies_all_ten_sections_resume_and_repeat(self):
        companies = records(3751)
        keys, scopes, checkpoint, accounted = set(), set(), None, 0
        pages = 0
        while True:
            page = plan_page(companies, policy=POLICY, as_of=NOW, after=checkpoint, limit=137, existing_keys=keys)
            for job in page.jobs:
                self.assertNotIn(job.job_key, keys)
                keys.add(job.job_key)
                scopes.add((job.company_id, job.section))
            accounted += page.sections_accounted
            checkpoint = page.checkpoint
            pages += 1
            if not page.has_more:
                break
        self.assertEqual((accounted, len(keys), len(scopes)), (37510, 37510, 37510))
        self.assertEqual(pages, 28)
        self.assertEqual(checkpoint, companies[-1].company_id)
        # Simulate a persisted first-page checkpoint and a new process with saved keys.
        repeated = plan_page(companies, policy=POLICY, as_of=NOW, limit=1000, existing_keys=keys)
        self.assertEqual(repeated.jobs, ())
        self.assertEqual(repeated.already_planned, 10000)
        full = {j.job_key for start in range(0, 3751, 1000) for j in plan_page(
            companies[start:start+1000], policy=POLICY, as_of=NOW, limit=1000).jobs}
        self.assertEqual(full, keys)

    def test_blocks_are_accounted_not_removed(self):
        c = records(3)
        c = (replace(c[0], status="excluded"), replace(c[1], manual_hold=True), replace(c[2], identity_ready=False))
        page = plan_page(c, policy=POLICY, as_of=NOW)
        self.assertEqual(page.sections_accounted, 30)
        self.assertEqual(sum(j.initial_state == "CANCELLED" for j in page.jobs), 10)
        self.assertEqual(sum(j.initial_state == "NEEDS_REVIEW" for j in page.jobs), 18)
        ready = [j.section for j in page.jobs if j.initial_state == "READY"]
        self.assertEqual(ready, ["IDENTITY", "SOURCES"])

    def test_new_event_invalidates_only_changed_section(self):
        c = records(1)
        prior = {(c[0].company_id, s): SectionState("HAS_HOLD_CLAIMS", NOW+timedelta(days=10), c[0].version)
                 for s in SECTIONS}
        page = plan_page(c, policy=POLICY, as_of=NOW, section_states=prior)
        self.assertEqual(page.not_due, 10)
        changed = {(c[0].company_id, "EVENTS"): digest("new event")}
        page = plan_page(c, policy=POLICY, as_of=NOW, section_states=prior, section_inputs=changed)
        self.assertEqual([j.section for j in page.jobs], ["EVENTS"])
        self.assertEqual(page.not_due, 9)

    def test_time_does_not_create_jobs_inside_same_due_window(self):
        a = plan_page(records(1), policy=POLICY, as_of=NOW)
        b = plan_page(records(1), policy=POLICY, as_of=NOW+timedelta(seconds=1))
        self.assertEqual([j.job_key for j in a.jobs], [j.job_key for j in b.jobs])

    def test_changed_input_and_policy_have_new_keys(self):
        c = records(1)
        a = plan_page(c, policy=POLICY, as_of=NOW)
        b = plan_page((replace(c[0], version=digest("new")),), policy=POLICY, as_of=NOW)
        d = plan_page(c, policy=Policy(version="continuous_v1"), as_of=NOW)
        for other in (b, d):
            self.assertTrue(set(j.job_key for j in a.jobs).isdisjoint(j.job_key for j in other.jobs))

    def test_invalid_order_duplicates_and_limits_rejected(self):
        for c, limit in ((records(2)[::-1], 10), (records(1)*2, 10), (records(1), 0), (records(1), 1001)):
            with self.subTest(limit=limit), self.assertRaises(ContractError):
                plan_page(c, policy=POLICY, as_of=NOW, limit=limit)

    def test_empty_page_preserves_checkpoint(self):
        page = plan_page((), policy=POLICY, as_of=NOW, after="synthetic_00001")
        self.assertEqual(page.checkpoint, "synthetic_00001")
        self.assertFalse(page.has_more)
        self.assertEqual(page.sections_accounted, 0)

    def test_new_id_behind_cursor_requires_and_is_seen_by_resweep(self):
        page = plan_page(records(2), policy=POLICY, as_of=NOW, after="synthetic_00001")
        self.assertEqual(page.companies_considered, 0)
        sweep = plan_page(records(2), policy=POLICY, as_of=NOW)
        self.assertEqual(sweep.companies_considered, 2)

    def test_section_next_due(self):
        self.assertEqual(next_due("EVENTS", NOW), NOW+timedelta(days=7))
        self.assertEqual(next_due("IDENTITY", NOW), NOW+timedelta(days=90))
        with self.assertRaises(ContractError):
            next_due("UNKNOWN", NOW)

    def test_discovery_period_cursor_disable_and_repeat(self):
        args = dict(source_ref="synthetic-awards", cursor="1", source_version="v1", as_of=NOW, policy=POLICY)
        a = discovery_job(**args)
        self.assertEqual(a.queue, "discovery")
        self.assertIsNone(discovery_job(**args, existing_keys={a.job_key}))
        self.assertNotEqual(discovery_job(**(args | {"cursor": "2"})).job_key, a.job_key)
        self.assertNotEqual(discovery_job(**(args | {"as_of": NOW+timedelta(hours=1)})).job_key, a.job_key)
        self.assertEqual(discovery_job(**args, disabled=True).initial_state, "CANCELLED")
        with self.assertRaises(ContractError):
            discovery_job(**args, interval_seconds=0)
