from dataclasses import replace
import unittest

from collectors.companies.model import Mention
from collectors.companies.resolve import resolve
from collectors.companies.store import SqliteStore
from collectors.companies.automation.contracts import IdentityEvidence, digest


def proof(name="Example Rotor GmbH", country="DE", **kwargs):
    defaults = dict(source_url="https://rotor.example.test/news", source_hash=digest("public synthetic source"),
                    source_kind="official_company", entity_kind="company", industry_relevant=True,
                    observed_name=name, observed_country=country)
    defaults.update(kwargs)
    return IdentityEvidence(**defaults)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteStore()
        self.addCleanup(self.store.close)
        self.oid, _ = self.store.add_observation("synthetic", "one", {"synthetic": True})

    def seed(self, name="Example Rotor GmbH", country="DE", website=None, ids=(), **kwargs):
        cid = self.store.create_company(name, country=country, website=website, **kwargs)
        for scheme, value in ids:
            self.store.add_identifier(cid, scheme, value, "synthetic_registry")
        return cid

    def strict(self, mention=None, evidence=None):
        return resolve(self.store, mention or Mention("Example Rotor GmbH", country="DE"), self.oid,
                       policy="continuous_v0", evidence=evidence or proof())

    def test_name_country_is_not_identity(self):
        cid = self.seed()
        self.assertEqual(self.strict(), (None, "candidate"))
        self.assertEqual(len(self.store.query("SELECT id FROM company")), 1)
        self.assertEqual(self.store.query("SELECT company_id FROM match_candidate")[0]["company_id"], cid)

    def test_legacy_caller_unchanged(self):
        cid = self.seed()
        self.assertEqual(resolve(self.store, Mention("Example Rotor GmbH", country="DE"), self.oid), (cid, "matched"))

    def test_unknown_policy_is_error(self):
        with self.assertRaisesRegex(ValueError, "UNKNOWN_IDENTITY_POLICY"):
            resolve(self.store, Mention("Example"), self.oid, policy="typo")

    def test_no_receipt_no_create(self):
        self.assertEqual(resolve(self.store, Mention("Example", country="DE"), self.oid,
                                 policy="continuous_v0"), (None, "candidate"))
        self.assertEqual(self.store.query("SELECT id FROM company"), [])

    def test_unique_official_domain_link(self):
        cid = self.seed(website="https://rotor.example.test")
        self.assertEqual(self.strict(), (cid, "matched"))

    def test_alias_can_link_when_exact_and_unique(self):
        cid = self.seed(name="Example Rotor Legal GmbH", website="https://rotor.example.test")
        self.store.add_alias(cid, "Example Rotor GmbH", "official_registry")
        self.assertEqual(self.strict(), (cid, "matched"))

    def test_news_domain_does_not_confirm_without_id(self):
        self.seed(website="https://rotor.example.test")
        self.assertEqual(self.strict(evidence=proof(source_kind="news")), (None, "candidate"))

    def test_shared_group_domain_is_ambiguous(self):
        self.seed(website="https://rotor.example.test")
        self.seed(name="Example Group Child GmbH", website="https://rotor.example.test")
        self.assertEqual(self.strict(), (None, "candidate"))
        self.assertEqual(len(self.store.query("SELECT id FROM company")), 2)

    def test_duplicate_same_names_cannot_link_by_domain(self):
        self.seed(website="https://rotor.example.test")
        self.seed(website="https://different.example.test")
        self.assertEqual(self.strict(), (None, "candidate"))

    def test_legal_form_stripping_does_not_confirm(self):
        self.seed(website="https://rotor.example.test")
        self.assertEqual(self.strict(Mention("Example Rotor", country="DE"), proof(name="Example Rotor")),
                         (None, "candidate"))

    def test_full_documented_identifier_confirms(self):
        cid = self.seed(ids=(("LEI", "SYNTHETIC-LEI-A"),))
        m = Mention("Example Rotor GmbH", country="DE", identifiers=[("LEI", "SYNTHETIC-LEI-A")])
        e = proof(document_identifiers=(("LEI", "SYNTHETIC-LEI-A"),), legal_entity_schemes=("LEI",))
        self.assertEqual(self.strict(m, e), (cid, "matched"))
        self.assertEqual(self.strict(m, replace(e, source_kind="news")), (cid, "matched"))

    def test_identifier_not_in_source_does_not_confirm(self):
        self.seed(ids=(("LEI", "A"),))
        m = Mention("Example Rotor GmbH", country="DE", identifiers=[("LEI", "A")])
        self.assertEqual(self.strict(m), (None, "candidate"))

    def test_conflict_even_when_first_identifier_matches(self):
        self.seed(ids=(("LEI", "A"), ("UEI", "B")))
        m = Mention("Example Rotor GmbH", country="DE", identifiers=[("LEI", "A"), ("UEI", "C")])
        e = proof(document_identifiers=(("LEI", "A"), ("UEI", "C")), legal_entity_schemes=("LEI", "UEI"))
        self.assertEqual(self.strict(m, e), (None, "candidate"))

    def test_two_identifiers_owned_by_different_companies(self):
        self.seed(ids=(("LEI", "A"),))
        self.seed(name="Example Other GmbH", ids=(("UEI", "B"),))
        m = Mention("Example Rotor GmbH", country="DE", identifiers=[("LEI", "A"), ("UEI", "B")])
        e = proof(document_identifiers=(("LEI", "A"), ("UEI", "B")), legal_entity_schemes=("LEI", "UEI"))
        self.assertEqual(self.strict(m, e), (None, "candidate"))
        self.assertEqual(self.store.company_by_identifier("LEI", "A"), self.store.query(
            "SELECT company_id FROM company_identifier WHERE scheme='LEI'")[0]["company_id"])

    def test_official_legal_new_is_candidate_only_and_repeat_no_duplicate(self):
        m = Mention("Example Rotor GmbH", country="DE", identifiers=[("LEI", "NEW")])
        e = proof(source_kind="official_registry", document_identifiers=(("LEI", "NEW"),), legal_entity_schemes=("LEI",))
        cid, outcome = self.strict(m, e)
        self.assertEqual(outcome, "new")
        self.assertEqual(self.store.get(cid)["status"], "candidate")
        self.assertEqual(self.strict(m, e), (None, "candidate"))
        self.assertEqual(len(self.store.query("SELECT id FROM company")), 1)

    def test_news_only_never_creates_legal_entity(self):
        m = Mention("Example Rotor GmbH", country="DE", identifiers=[("LEI", "NEW")])
        e = proof(source_kind="news", document_identifiers=(("LEI", "NEW"),), legal_entity_schemes=("LEI",))
        self.assertEqual(self.strict(m, e), (None, "candidate"))
        self.assertEqual(self.store.query("SELECT id FROM company"), [])

    def test_facility_scheme_without_legal_admission_not_enough(self):
        self.seed(ids=(("CAGE", "SITE-A"),))
        m = Mention("Example Rotor GmbH", country="DE", identifiers=[("CAGE", "SITE-A")])
        self.assertEqual(self.strict(m, proof(document_identifiers=(("CAGE", "SITE-A"),))), (None, "candidate"))

    def test_type_and_role_counterexamples(self):
        for changes in ({"entity_kind": "product"}, {"entity_kind": "army"}, {"entity_kind": "government"},
                        {"industry_relevant": False}, {"expected_role": "supplier", "observed_role": "buyer"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.strict(evidence=proof(**changes)), (None, "candidate"))
        self.assertEqual(self.store.query("SELECT id FROM company"), [])

    def test_country_or_source_subject_mismatch(self):
        self.seed(website="https://rotor.example.test")
        for e in (proof(country="AT"), proof(name="Example Other GmbH")):
            self.assertEqual(self.strict(evidence=e), (None, "candidate"))

    def test_excluded_record_stays_excluded(self):
        cid = self.seed(website="https://rotor.example.test")
        self.store.update(cid, status="excluded", excluded_reason="synthetic policy")
        self.assertEqual(self.strict(), (None, "candidate"))
        self.assertEqual(self.store.get(cid)["status"], "excluded")

    def test_existing_notion_binding_never_recreates(self):
        cid = self.seed(notion_page_id="synthetic-page-1")
        m = Mention("Example Rotor GmbH", country="DE", notion_page_id="synthetic-page-1")
        e = proof(source_kind="saved_binding", bound_page_id="synthetic-page-1")
        self.assertEqual(self.strict(m, e), (cid, "matched"))
        self.assertEqual(self.strict(m, replace(e, bound_page_id="other")), (None, "candidate"))

    def test_ua_city_not_created(self):
        m = Mention("Example UA Systems", country="UA", city="SENSITIVE SYNTHETIC", identifiers=[("EDRPOU", "FAKE")])
        e = proof(name=m.name, country="UA", source_kind="official_registry",
                  document_identifiers=(("EDRPOU", "FAKE"),), legal_entity_schemes=("EDRPOU",))
        cid, outcome = self.strict(m, e)
        self.assertEqual(outcome, "new")
        self.assertIsNone(self.store.get(cid)["city"])

    def test_excluded_country_no_create(self):
        m = Mention("Example RU", country="RU", identifiers=[("LEI", "FAKE")])
        e = proof(name=m.name, country="RU", source_kind="official_registry",
                  document_identifiers=(("LEI", "FAKE"),), legal_entity_schemes=("LEI",))
        self.assertEqual(self.strict(m, e), (None, "candidate"))

    def test_notion_binding_and_documented_identifier_conflict(self):
        self.seed(notion_page_id="page-A")
        self.seed(name="Example Other GmbH", ids=(("LEI", "B"),))
        m = Mention("Example Rotor GmbH", country="DE", notion_page_id="page-A", identifiers=[("LEI", "B")])
        e = proof(document_identifiers=(("LEI", "B"),), legal_entity_schemes=("LEI",))
        self.assertEqual(self.strict(m, e), (None, "candidate"))

    def test_national_jurisdiction_and_conflicting_scheme_values(self):
        for ids in ((("NATIONAL:AT", "A"),), (("LEI", "A"), ("LEI", "B"))):
            with self.subTest(ids=ids):
                m = Mention("Example Rotor GmbH", country="DE", identifiers=list(ids))
                e = proof(source_kind="official_registry", document_identifiers=ids,
                          legal_entity_schemes=tuple(s for s, _ in ids))
                self.assertEqual(self.strict(m, e), (None, "candidate"))
        self.assertEqual(self.store.query("SELECT id FROM company"), [])
