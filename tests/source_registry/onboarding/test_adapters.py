import copy
import unittest

from helpers import adapters, contracts as c, fixture, mapped


class AdapterTests(unittest.TestCase):
    def test_grants_keeps_errors_scope_metadata_and_deadlines(self):
        report = fixture("grants_synthetic.json")
        report["errors"] = {"synthetic-keyword": "TimeoutError"}
        result = mapped(report=report)
        self.assertEqual(result["retrieval_status"], "PARTIAL")
        self.assertEqual(result["errors"], report["errors"])
        self.assertEqual(result["records"][0]["payload"], report["items"][0])
        self.assertEqual(result["records"][0]["source_time"]["timezone"], "UNKNOWN")
        self.assertIsNone(result["records"][0]["native_version"])
        self.assertEqual(result["coverage_status"], "UNVERIFIED")

    def test_openalex_retains_doi_query_and_unverified_relevance(self):
        report = fixture("openalex_synthetic.json")
        result = mapped("openalex", report)
        record = result["records"][0]
        self.assertEqual(record["record_kind"], "SCHOLARLY_METADATA")
        self.assertEqual(record["payload"]["doi"], report["items"][0]["doi"])
        self.assertFalse(record["payload"]["is_verified_relevance"])
        self.assertEqual(record["relevance"], "UNVERIFIED")
        self.assertEqual(result["scope"]["page_selection"], "FIRST_PAGE_ONLY")
        self.assertEqual(result["scope"]["since"], report["since"])

    def test_presence_missing_null_empty_invalid_are_different(self):
        report = fixture("grants_synthetic.json")
        row = report["items"][0]
        del row["agency"]; row["agency_code"] = None; row["title"] = ""; row["status"] = 42
        result = mapped(report=report)
        states = result["row_outcomes"][0]["field_presence"]
        self.assertEqual([states[k] for k in ("agency", "agency_code", "title", "status")],
                         ["MISSING", "NULL", "EMPTY", "INVALID"])
        self.assertEqual(result["counts"]["quarantined"], 1)

    def test_unkeyed_and_nonobject_rows_remain_in_audit(self):
        report = fixture("grants_synthetic.json")
        report["items"] = [{"title": "unknown ID"}, None, "bad", {"external_id": 2}]
        result = mapped(report=report)
        self.assertEqual(result["counts"]["input_occurrences"], 4)
        self.assertEqual(result["counts"]["quarantined"], 4)
        self.assertEqual([o["input_index"] for o in result["row_outcomes"]], list(range(4)))

    def test_duplicate_vs_collision_holds_all_conflicting_versions(self):
        report = fixture("grants_synthetic.json")
        row = report["items"][0]; changed = copy.deepcopy(row); changed["status"] = "changed"
        report["items"] = [row, copy.deepcopy(row), changed]
        report["unique_results"] = 3
        result = mapped(report=report)
        self.assertEqual(result["counts"]["duplicates"], 1)
        self.assertEqual(len(result["records"]), 2)
        self.assertEqual(result["counts"]["unique_entity_keys"], 1)
        self.assertTrue(all(r["structural_qa"]["status"] == "HOLD" for r in result["records"]))
        self.assertTrue(all(o["collision"] for o in result["row_outcomes"]))

    def test_same_name_different_ids_not_merged(self):
        report = fixture("grants_synthetic.json")
        row = copy.deepcopy(report["items"][0]); row["external_id"] = "900002"
        report["items"].append(row)
        self.assertEqual(len(mapped(report=report)["records"]), 2)

    def test_query_changes_do_not_create_revisions_and_evidence_is_not_lost(self):
        for adapter, field, value in (("grants", "matched_keywords", ["another synthetic keyword"]),
                                       ("openalex", "discovery_query", "another synthetic query")):
            with self.subTest(adapter=adapter):
                report = fixture(adapter + "_synthetic.json")
                first = mapped(adapter, report)["records"][0]["record_ref"]
                other = copy.deepcopy(report["items"][0]); other[field] = value
                report["items"].append(other)
                result = mapped(adapter, report)
                self.assertEqual(result["counts"]["duplicates"], 1)
                self.assertEqual(result["records"][0]["record_ref"], first)
                self.assertEqual(len(result["records"]), 1)
                observations = result["records"][0]["provenance"]["discovery_observations"]
                self.assertEqual(len(observations), 2)
                self.assertEqual(observations[1][field], value)

    def test_missing_or_malformed_errors_never_default_to_success(self):
        for adapter in ("grants", "openalex"):
            for action in ("missing", "wrong_type", "bad_entry"):
                with self.subTest(adapter=adapter, action=action):
                    report = fixture(adapter + "_synthetic.json")
                    if action == "missing": del report["errors"]
                    if action == "wrong_type": report["errors"] = None
                    if action == "bad_entry": report["errors"] = {"a": 2} if adapter == "grants" else [{}]
                    with self.assertRaises(c.ContractError): mapped(adapter, report)

    def test_empty_failed_and_source_count_mismatch_are_explicit(self):
        report = fixture("grants_synthetic.json"); report["items"] = []; report["unique_results"] = 0
        self.assertEqual(mapped(report=report)["retrieval_status"], "EMPTY")
        report["errors"] = {"x": "HTTPError"}
        self.assertEqual(mapped(report=report)["retrieval_status"], "FAILED")
        report = fixture("grants_synthetic.json"); report["unique_results"] = 999
        self.assertIn("SOURCE_COUNT_MISMATCH", mapped(report=report)["limitations"])

    def test_strict_schema_profile_ids_and_row_bounds(self):
        report = fixture("grants_synthetic.json")
        mutations = [dict(report, items=[{}] * 501), dict(report, source="Not grants"),
                     dict(report, keyword_count=True), dict(report, source_id=2)]
        for bad in mutations:
            with self.assertRaises(c.ContractError): mapped(report=bad)
        report["items"][0]["title"] = "x" * (c.MAX_ROW_BYTES + 1)
        with self.assertRaisesRegex(c.ContractError, "ROW_SIZE"): mapped(report=report)
        for native in ("https://example.com/W1", "W1", "https://openalex.org/notwork"):
            report = fixture("openalex_synthetic.json"); report["items"][0]["external_id"] = native
            self.assertEqual(mapped("openalex", report)["counts"]["quarantined"], 1)

    def test_force_source2_or_wrong_profile_is_rejected(self):
        p = adapters.profile("grants", "c" * 64); p["source_id"] = 2
        with self.assertRaisesRegex(c.ContractError, "PROFILE_MISMATCH"):
            adapters.adapt(fixture("grants_synthetic.json"), "grants", p, "2026-10-24T00:00:00Z")
