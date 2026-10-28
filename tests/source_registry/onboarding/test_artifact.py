import copy
import unittest

from helpers import (WHEN, PRODUCER, artifact, audit, bundle, contracts as c, inputs,
                     master, registry)


class ArtifactTests(unittest.TestCase):
    def test_input_hash_is_not_api_response_hash(self):
        data = master(); projection = registry.project_markdown(data, c.sha256(data))
        a = artifact.build_artifact(inputs()["grants"], "grants", projection, PRODUCER, WHEN)
        self.assertEqual(a["input_manifest"]["input_sha256"], c.sha256(inputs()["grants"]))
        self.assertEqual(a["source_response_hashes"], [])
        self.assertIn("SOURCE_BYTES_NOT_AVAILABLE", a["limitations"])
        self.assertEqual(a["producer_tree_state"], "DIRTY_ISOLATED")
        self.assertEqual(a["record_count"], len(a["records"]))

    def test_repeated_input_and_observation_time_have_stable_candidate_identity(self):
        data = master(); projection = registry.project_markdown(data, c.sha256(data))
        a = artifact.build_artifact(inputs()["openalex"], "openalex", projection, PRODUCER, WHEN)
        b = artifact.build_artifact(inputs()["openalex"], "openalex", projection, PRODUCER, WHEN)
        self.assertEqual(a, b)
        later = artifact.build_artifact(inputs()["openalex"], "openalex", projection, PRODUCER,
                                        "2026-10-25T00:00:00Z")
        self.assertNotEqual(a["artifact_id"], later["artifact_id"])
        self.assertEqual(a["records"][0]["record_ref"], later["records"][0]["record_ref"])
        self.assertEqual(a["query_fingerprint"], later["query_fingerprint"])

    def test_proposals_are_no_write_and_unknowns_hold(self):
        b = bundle(); report = c.parse_json(b["audit_report.json"])
        for a in report["artifacts"]:
            for p in artifact.destination_proposals(a):
                self.assertEqual(p["operation"], "NO_WRITE")
                self.assertFalse(p["eligible_for_future_review"])
                self.assertFalse(p["public_release"])
                self.assertIsNone(p["target_contract_ref"])
                self.assertIn("DESTINATION_NOT_ADMITTED", p["blocked_reasons"])
        self.assertEqual(report["checkpoint"], "NOT_OWNED_NO_ADVANCEMENT")
        self.assertEqual(report["status"], "HOLD")

    def test_unknown_envelope_version_and_slot_mismatch_rejected(self):
        projection = registry.project_markdown(master(), c.sha256(master()))
        for change in ("version", "slot"):
            p = copy.deepcopy(projection)
            if change == "version": p["schema_version"] = "2.0.0"
            else: p["slots"][3]["source_profile_label"] = "Wrong publisher"
            with self.assertRaises(c.ContractError):
                artifact.build_artifact(inputs()["grants"], "grants", p, PRODUCER, WHEN)

    def test_invalid_producer_and_oversize_input_rejected(self):
        for key, value in (("version", "2"), ("git_sha", "main"), ("code_hash", "unknown"),
                           ("tree_state", "CLEAN")):
            p = dict(PRODUCER); p[key] = value
            with self.assertRaises(c.ContractError):
                audit.prepare_bundle(master(), c.sha256(master()), inputs(), p, WHEN)
        with self.assertRaisesRegex(c.ContractError, "INPUT_SIZE"):
            c.parse_json(b" " * (c.MAX_BYTES + 1))

    def test_manifest_hashes_four_files_without_self_cycle(self):
        b = bundle(); manifest = audit.validate_bundle(b)
        self.assertEqual(set(manifest["files"]), set(audit.FILES))
        self.assertNotIn("run_manifest.json", manifest["files"])
        for name, h in manifest["files"].items(): self.assertEqual(h, c.sha256(b[name]))

    def test_hash_mismatch_and_partial_jsonl_are_rejected(self):
        b = bundle(); b["records.jsonl"] += b"x"
        with self.assertRaisesRegex(c.ContractError, "MANIFEST_MISMATCH"): audit.validate_bundle(b)
        b = bundle(); b["records.jsonl"] = b["records.jsonl"].rstrip(b"\n")
        self.rehash(b)
        with self.assertRaisesRegex(c.ContractError, "INCOMPLETE_JSONL"): audit.validate_bundle(b)

    @staticmethod
    def rehash(b):
        m = c.parse_json(b["run_manifest.json"])
        m["files"] = {n: c.sha256(b[n]) for n in audit.FILES}
        b["run_manifest.json"] = c.canonical(m) + b"\n"

    def test_cross_references_and_forced_write_rejected_even_after_rehash(self):
        for change in ("record", "proposal", "version", "release"):
            with self.subTest(change=change):
                b = bundle()
                if change == "record": b["records.jsonl"] = b""
                if change == "proposal":
                    rows = [c.parse_json(x) for x in b["destination_proposals.jsonl"].splitlines()]
                    rows[0]["operation"] = "INSERT"
                    b["destination_proposals.jsonl"] = b"".join(c.canonical(x)+b"\n" for x in rows)
                if change in ("version", "release"):
                    report = c.parse_json(b["audit_report.json"])
                    if change == "version": report["artifacts"][0]["schema_version"] = "2.0.0"
                    else: report["public_release"] = True
                    b["audit_report.json"] = c.canonical(report)+b"\n"
                self.rehash(b)
                with self.assertRaises(c.ContractError): audit.validate_bundle(b)

    def test_invalid_output_shapes_missing_fields_and_fabricated_readiness_rejected(self):
        for case in ("manifest_null", "extra_field", "missing_errors", "fake_complete", "readiness"):
            with self.subTest(case=case):
                b = bundle()
                if case == "manifest_null": b["run_manifest.json"] = b"null\n"
                elif case == "readiness":
                    p = c.parse_json(b["registry_projection.json"])
                    p["slots"][0]["readiness"]["access"]["status"] = "PASS"
                    b["registry_projection.json"] = c.canonical(p)+b"\n"; self.rehash(b)
                else:
                    report = c.parse_json(b["audit_report.json"])
                    if case == "extra_field": report["source_id"] = 2
                    if case == "missing_errors": del report["artifacts"][0]["errors"]
                    if case == "fake_complete": report["artifacts"][0]["retrieval_status"] = "COMPLETE"
                    b["audit_report.json"] = c.canonical(report)+b"\n"; self.rehash(b)
                with self.assertRaises(c.ContractError): audit.validate_bundle(b)
