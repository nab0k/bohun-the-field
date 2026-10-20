"""Offline metadata-only normalization checks; no live sources."""
import unittest

from normalize import NormalizationError, uk_release, canada_row, eu_result, prepare


class NormalizeTests(unittest.TestCase):
    def test_uk_procedure_and_award_separate(self):
        base = {"ocid": "ocds-demo", "id": "release-1",
                "tender": {"title": "Synthetic tender"}, "tag": ["tender"]}
        result = uk_release(base)
        self.assertEqual(result["item_type_candidate"], "procurement_notice")
        self.assertEqual(result["external_id"], "ocds-demo::release-1")
        self.assertEqual(result["verification_status"], "HOLD")
        self.assertFalse(result["public_release"])
        award = {**base, "id": "release-2", "tag": ["award"]}
        self.assertEqual(uk_release(award)["item_type_candidate"], "contract_award_notice")

    def test_uk_missing_id_fails(self):
        with self.assertRaises(NormalizationError):
            uk_release({"tender": {"title": "Missing identifiers"}})

    def test_canada_missing_reference_fails(self):
        with self.assertRaises(NormalizationError):
            canada_row({"title-titre-eng": "Example"}, dataset="ca_new")

    def test_canada_existing_federal_notice(self):
        row = {"referenceNumber-numeroReference": "CA-1",
               "title-titre-eng": "Test"}
        a = canada_row(row, dataset="ca_new")
        b = canada_row(row, dataset="ca_open")
        self.assertEqual(a["source_record_sha256"], b["source_record_sha256"])
        self.assertFalse(a["public_release"])

    def test_eu_unrecognized_type_not_guessing(self):
        out = eu_result({"reference": "EU-1", "metadata": {"title": ["Synthetic call"],
                                                            "type": ["UNRECOGNIZED"]}})
        self.assertEqual(out["item_type_candidate"], "eu_unclassified")

    def test_deduplicate_exact_same_uk_release(self):
        row = {"ocid": "x", "id": "a", "tag": ["tender"],
               "tender": {"title": "Demo"}}
        out = prepare("uk_fts", {"releases": [row, row]}, scope="one_hour")
        self.assertEqual(len(out["items"]), 1)

    def test_conflicting_same_uk_release_held(self):
        a = {"ocid": "x", "id": "a", "tag": ["tender"],
             "tender": {"title": "Demo"}}
        b = {**a, "tender": {"title": "Other"}}
        with self.assertRaisesRegex(NormalizationError, "conflicting_versions"):
            prepare("uk_fts", {"releases": [a, b]}, scope="one_hour")

    def test_eu_incomplete_is_not_full_snapshot(self):
        with self.assertRaisesRegex(NormalizationError, "incomplete"):
            prepare("eu_funding", {
                "results": [{"reference": "a", "metadata": {"title": ["x"]}}],
                "totalResults": 4
            }, scope="edf_grants")

    def test_canada_scope_mandatory(self):
        with self.assertRaisesRegex(NormalizationError, "ca_scope_required"):
            prepare("canadabuys", [], scope="ca_awards")

    def test_publication_false_everywhere(self):
        examples = [
            uk_release({"ocid": "x", "id": "1", "tender": {"title": "UK"}, "tag": ["tender"]}),
            canada_row({"referenceNumber-numeroReference": "ref", "title-titre-eng": "CA"},
                       dataset="ca_open"),
            eu_result({"reference": "ref", "metadata": {"title": ["EU"], "type": ["1"]}}),
        ]
        self.assertTrue(all(x["verification_status"] == "HOLD"
                            and x["capture_status"] == "METADATA_ONLY"
                            and not x["public_release"] for x in examples))


if __name__ == "__main__":
    unittest.main()
