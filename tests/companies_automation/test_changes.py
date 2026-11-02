from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from collectors.companies.automation.contracts import (
    CompanyView, ContractError, Evidence, FieldValue, Policy, ProposedClaim, Qualifiers,
    ResearchReceipt, SourceDocument, digest, https_host, json_text)
from collectors.companies.automation.changes import evaluate, check_preconditions

FIX = json.loads(Path(__file__).with_name("fixtures.json").read_text())
NOW = datetime.fromisoformat(FIX["date"])
POLICY = Policy()
CID = "co_synthetic_rotor"


def claim(*, section="PRODUCTS", path="/products/name", value="electric rotors", q=None,
          source_kind="official_company", source_url="https://rotor.example.test/about",
          ref="synthetic-claim", text=None):
    q = q or Qualifiers(as_of="2026-10-31")
    text = text or FIX["product_text"]
    source = SourceDocument("synthetic-source", source_url, text, NOW-timedelta(hours=1), source_kind)
    e = Evidence(source, source.content_hash, CID, "official_name_country_domain", 0, len(text), text, q,
                 path, json_text(value))
    return ProposedClaim(CID, section, ref, path, json_text(value), (e,), q)


def run_change(proposals, *, view=None, section=None, research=None):
    return evaluate(view or CompanyView(CID, country="DE"), proposals,
                    section=section or (proposals[0].section if proposals else "PRODUCTS"),
                    policy=POLICY, now=NOW, research=research)


def website_claim(**kwargs):
    return claim(section="IDENTITY", path="/identity/website", value="https://rotor.example.test",
                 text=FIX["website_text"], **kwargs)


class ChangeTests(unittest.TestCase):
    def test_company_statement_is_private_attributed_hold(self):
        change = run_change((claim(),))
        self.assertEqual(len(change.claims), 1)
        self.assertEqual(change.claims[0].assessment, "company_claim")
        self.assertEqual(change.claims[0].qa_status, "HOLD")
        self.assertIs(change.claims[0].public_release, False)
        self.assertEqual(change.coverage, "HAS_HOLD_CLAIMS")
        self.assertEqual(change.fills, ())

    def test_allowed_missing_fill_requires_claim_and_precondition(self):
        view = CompanyView(CID, country="DE")
        change = run_change((website_claim(),), view=view)
        self.assertEqual(len(change.fills), 1)
        check_preconditions(change, view, POLICY)

    def test_manual_value_even_blank_is_preserved(self):
        for field in (FieldValue(True, json_text("https://manual.example.test"), "manual"),
                      FieldValue(False, owner="manual"), FieldValue(True, "null", "manual")):
            with self.subTest(field=field):
                view = CompanyView(CID, country="DE", fields=(("/identity/website", field),))
                change = run_change((website_claim(),), view=view)
                self.assertEqual(change.fills, ())
                self.assertEqual(len(change.claims), 1)
                self.assertIn("MANUAL_VALUE_PROTECTED", [i.code for i in change.issues])

    def test_machine_nonempty_not_replaced(self):
        view = CompanyView(CID, country="DE", fields=(("/identity/website", FieldValue(
            True, json_text("https://previous.example.test"), "machine")),))
        change = run_change((website_claim(),), view=view)
        self.assertEqual(change.fills, ())
        self.assertIn("NONEMPTY_VALUE_CONFLICT", [i.code for i in change.issues])

    def test_stale_base_aborts_commit_instead_of_overwriting(self):
        view = CompanyView(CID, country="DE")
        change = run_change((website_claim(),), view=view)
        current = replace(view, fields=(("/identity/website", FieldValue(True, '"manual"', "manual")),))
        with self.assertRaisesRegex(ContractError, "STALE_CONTEXT"):
            check_preconditions(change, current, POLICY)

    def test_policy_changed_aborts_commit(self):
        view = CompanyView(CID, country="DE")
        with self.assertRaisesRegex(ContractError, "POLICY_CHANGED"):
            check_preconditions(run_change((website_claim(),), view=view), view, Policy(version="continuous_v1"))

    def test_cached_changes_cannot_cross_company_scope(self):
        view = CompanyView(CID, country="DE")
        result = run_change((website_claim(),), view=view)
        wrong_claim = replace(result.claims[0], proposal=replace(result.claims[0].proposal, company_id="other"))
        with self.assertRaisesRegex(ContractError, "COMMIT_CLAIM_SCOPE_INVALID"):
            check_preconditions(replace(result, claims=(wrong_claim,)), view, POLICY)

    def test_unknown_and_null_do_not_clear(self):
        for value in (None, ""):
            with self.subTest(value=value):
                change = run_change((replace(website_claim(), value_json=json_text(value)),))
                self.assertEqual((change.fills, change.claims), ((), ()))
                self.assertIsNone(change.reviewed_at)

    def test_rejected_claim_cannot_evade_by_ref_or_source_change(self):
        original = claim()
        alternate = replace(original, claim_ref="another-reference")
        self.assertEqual(original.content_hash, alternate.content_hash)
        view = CompanyView(CID, country="DE", rejected_hashes=(original.content_hash,))
        change = run_change((alternate,), view=view)
        self.assertEqual(change.claims, ())
        self.assertEqual(change.issues[0].code, "REJECTED_CONTENT")

    def test_wrong_company_on_claim_or_evidence_rejected(self):
        base = claim()
        cases = (replace(base, company_id="other-company"),
                 replace(base, evidence=(replace(base.evidence[0], company_id="other-company"),)))
        for proposal in cases:
            with self.subTest(proposal=proposal):
                self.assertEqual(run_change((proposal,)).claims, ())

    def test_quote_or_hash_tampering_rejected(self):
        base = claim()
        for e in (replace(base.evidence[0], quote="invented quote"),
                  replace(base.evidence[0], start=1),
                  replace(base.evidence[0], source_hash=digest("other"))):
            with self.subTest(e=e):
                change = run_change((replace(base, evidence=(e,)),))
                self.assertEqual(change.claims, ())
                self.assertEqual(change.coverage, "NOT_RESEARCHED")

    def test_utf8_quote_positions_are_character_offsets_hash_is_bytes(self):
        proposal = claim(text="Компанія Example Rotor GmbH заявляє про двигуни — тест.")
        self.assertEqual(len(run_change((proposal,)).claims), 1)

    def test_planned_delivery_and_negation_preserved(self):
        for qualifiers, text in ((Qualifiers(modality="planned", event_kind="delivery", as_of="2027-01-01"), FIX["plan_text"]),
                                 (Qualifiers(polarity="negative", event_kind="award"), FIX["negation_text"])):
            proposal = claim(section="EVENTS", path="/events/summary", q=qualifiers, text=text)
            change = run_change((proposal,))
            self.assertEqual(change.claims[0].proposal.qualifiers, qualifiers)
            # Changing the proposed event but retaining source extraction receipt is rejected.
            wrong = replace(proposal, qualifiers=Qualifiers(event_kind="delivery"))
            self.assertEqual(run_change((wrong,)).claims, ())

    def test_plan_or_negation_cannot_fill_base(self):
        for q in (Qualifiers(modality="planned"), Qualifiers(polarity="negative")):
            p = website_claim(q=q)
            result = run_change((p,))
            self.assertEqual(result.fills, ())
            self.assertEqual(len(result.claims), 1)

    def test_notice_award_contract_and_delivery_not_interchangeable(self):
        p = claim(section="PROGRAMMES", path="/programmes/status", value="award", q=Qualifiers(event_kind="award"))
        for event_kind in ("notice", "contract", "delivery"):
            bad = replace(p, qualifiers=Qualifiers(event_kind=event_kind))
            self.assertEqual(run_change((bad,)).claims, ())

    def test_amount_requires_currency_period_unit_and_matches_receipt(self):
        p = claim(section="FINANCIALS", path="/financials/amount", value=500000,
                  q=Qualifiers(event_kind="award", currency="EUR", unit="currency", period="2026"), text=FIX["amount_text"])
        self.assertEqual(len(run_change((p,)).claims), 1)
        wrong = replace(p, qualifiers=replace(p.qualifiers, currency="USD"))
        self.assertEqual(run_change((wrong,)).claims, ())
        p = claim(section="FINANCIALS", path="/financials/amount", value=500000, text=FIX["amount_text"])
        self.assertEqual(run_change((p,)).issues[0].code, "AMOUNT_CONTEXT_MISSING")

    def test_actual_future_fact_rejected_plan_retained(self):
        p = claim(q=Qualifiers(as_of="2027-01-01"))
        self.assertEqual(run_change((p,)).issues[0].code, "FUTURE_ACTUAL_FACT")

    def test_capture_from_future_rejected(self):
        p = claim()
        e = p.evidence[0]
        future = replace(e.source, captured_at=NOW+timedelta(minutes=1))
        result = run_change((replace(p, evidence=(replace(e, source=future),)),))
        self.assertEqual(result.issues[0].code, "CAPTURE_IN_FUTURE")

    def test_syndication_not_counted_as_independent(self):
        p = claim(source_kind="news")
        e = p.evidence[0]
        one = replace(e, source=replace(e.source, independence_group="agency-one"))
        two_source = SourceDocument("mirror", "https://mirror.example.test", e.quote+" mirror",
                                    NOW, "news", "agency-one")
        two = replace(e, source=two_source, source_hash=two_source.content_hash)
        result = run_change((replace(p, evidence=(one, two)),))
        self.assertEqual(result.claims[0].assessment, "inferred_needs_review")
        two = replace(two, source=replace(two.source, independence_group="independent-other"))
        result = run_change((replace(p, evidence=(one, two)),))
        self.assertEqual(result.claims[0].assessment, "cross_source")
        self.assertEqual(result.claims[0].qa_status, "HOLD")

    def test_news_only_does_not_fill_website(self):
        result = run_change((website_claim(source_kind="news"),))
        self.assertEqual(result.fills, ())
        self.assertEqual(len(result.claims), 1)

    def test_approved_coverage_not_downgraded(self):
        view = CompanyView(CID, country="DE", section_states=(("PRODUCTS", "HAS_APPROVED_CLAIMS"),))
        self.assertEqual(run_change((claim(),), view=view).coverage, "HAS_APPROVED_CLAIMS")

    def test_no_data_is_not_researched_not_found(self):
        self.assertEqual(run_change(()).coverage, "NOT_RESEARCHED")
        source = claim().evidence[0].source
        for complete, no_results, checked in ((False, True, (source,)), (True, False, (source,)), (True, True, ())):
            receipt = ResearchReceipt(CID, "PRODUCTS", "bounded public-source search", complete, no_results, checked)
            result = run_change((), research=receipt)
            self.assertEqual(result.coverage, "NOT_RESEARCHED")
            self.assertIsNone(result.reviewed_at)

    def test_bounded_completed_search_can_record_not_found(self):
        receipt = ResearchReceipt(CID, "PRODUCTS", "official sources only", True, True, (claim().evidence[0].source,))
        result = run_change((), research=receipt)
        self.assertEqual(result.coverage, "RESEARCHED_NOT_FOUND")
        self.assertIsNotNone(result.reviewed_at)

    def test_invalid_claim_does_not_become_not_found(self):
        p = replace(claim(), evidence=())
        receipt = ResearchReceipt(CID, "PRODUCTS", "official sources", True, True, (claim().evidence[0].source,))
        self.assertEqual(run_change((p,), research=receipt).coverage, "NOT_RESEARCHED")

    def test_ua_and_unknown_precise_geography_blocked(self):
        p = claim(section="GEOGRAPHY", path="/geography/site", value="SYNTHETIC COORDINATES")
        for country in ("UA", None):
            with self.subTest(country=country):
                result = run_change((p,), view=CompanyView(CID, country=country))
                self.assertEqual(result.claims, ())
                self.assertEqual(result.issues[0].code, "SENSITIVE_OR_UNRESOLVED_GEOGRAPHY")

    def test_protected_record_no_changes(self):
        result = run_change((claim(),), view=CompanyView(CID, country="DE", protected=True))
        self.assertEqual((result.claims, result.fills), ((), ()))

    def test_country_conflict_not_filled(self):
        result = run_change((claim(section="IDENTITY", path="/identity/country", value="AT"),))
        self.assertEqual(result.fills, ())
        self.assertIn("COUNTRY_CONFLICT", [i.code for i in result.issues])

    def test_conflicting_batch_values_never_pick_last(self):
        p = website_claim()
        other = claim(section="IDENTITY", path="/identity/website", ref="second", value="https://second.example.test")
        again = claim(section="IDENTITY", path="/identity/website", ref="third", value="https://third.example.test")
        result = run_change((p, other, again))
        self.assertEqual(result.fills, ())
        self.assertEqual(len(result.claims), 3)

    def test_duplicate_content_once_and_duplicate_reference_error(self):
        p = claim()
        result = run_change((p, replace(p, claim_ref="alias")))
        self.assertEqual(len(result.claims), 1)
        with self.assertRaisesRegex(ContractError, "DUPLICATE_CLAIM_REF"):
            run_change((p, p))

    def test_value_or_field_cannot_diverge_from_extraction_receipt(self):
        p = claim()
        for changed in (replace(p, value_json=json_text("different product")),
                        replace(p, field_path="/products/other"),
                        replace(p, evidence=(replace(p.evidence[0], observed_value_json=None),))):
            with self.subTest(changed=changed):
                result = run_change((changed,))
                self.assertEqual(result.claims, ())
                self.assertEqual(result.issues[0].code, "EXTRACTED_VALUE_CONFLICT")

    def test_section_scope_and_path_checked(self):
        p = claim()
        self.assertEqual(run_change((p,), section="FINANCIALS").claims, ())
        self.assertEqual(run_change((replace(p, field_path="/financials/name"),)).claims, ())

    def test_no_missing_evidence_and_no_infinite_number(self):
        self.assertEqual(run_change((replace(claim(), evidence=()),)).claims, ())
        with self.assertRaises(ContractError):
            claim(value=float("nan"))

    def test_url_and_date_input_guards(self):
        for url in ("http://example.test", "https://127.0.0.1", "https://localhost", "https://user:pass@example.test",
                    "https://example.test:8080", "https://example.test/#secret", "https://example.test/\n"):
            with self.subTest(url=url), self.assertRaises(ContractError):
                https_host(url)
        for date in ("2026-02-30", "2026-01", "not-a-date"):
            with self.subTest(date=date), self.assertRaises(ContractError):
                Qualifiers(as_of=date)
