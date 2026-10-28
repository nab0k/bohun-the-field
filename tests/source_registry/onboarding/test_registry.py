import copy
import unittest

from helpers import contracts as c, fixture, master, registry


class RegistryTests(unittest.TestCase):
    def test_42_slots_keep_distinct_identities_and_unknown_readiness(self):
        projection = registry.project_synthetic(fixture("registry_sources_synthetic.json"))
        self.assertEqual(len(projection["slots"]), 42)
        first, second = projection["slots"][:2]
        self.assertEqual(first["source_profile_label"], second["source_profile_label"])
        self.assertNotEqual(first["slot_id"], second["slot_id"])
        for slot in projection["slots"]:
            self.assertIsNone(slot["operational_profile"])
            self.assertEqual({o["status"] for o in slot["readiness"].values()}, {"UNKNOWN"})

    def test_master_hash_and_columns_are_review_gates(self):
        data = master()
        self.assertEqual(len(registry.project_markdown(data, c.sha256(data))["slots"]), 42)
        with self.assertRaisesRegex(c.ContractError, "MASTER_CHANGED"):
            registry.project_markdown(data + b" ", c.sha256(data))
        changed = data.replace(b"Evidence /", b"Unknown /")
        with self.assertRaisesRegex(c.ContractError, "MASTER_COLUMNS"):
            registry.project_markdown(changed, c.sha256(changed))

    def test_duplicate_reordered_missing_and_extra_ids_rejected(self):
        for change in ("duplicate", "order", "missing", "extra", "column", "priority"):
            with self.subTest(change=change):
                f = fixture("registry_sources_synthetic.json")
                if change == "duplicate": f["rows"][1][0] = "S01"
                if change == "order": f["rows"][1], f["rows"][0] = f["rows"][0], f["rows"][1]
                if change == "missing": f["rows"].pop()
                if change == "extra": f["rows"].append(copy.deepcopy(f["rows"][0]))
                if change == "column": f["rows"][0].append("extra")
                if change == "priority": f["rows"][0][3] = "R9"
                with self.assertRaises(c.ContractError): registry.project_synthetic(f)

    def test_unknown_version_and_false_synthetic_fixture_rejected(self):
        for key, value in (("schema_version", "2.0.0"), ("synthetic", False)):
            f = fixture("registry_sources_synthetic.json"); f[key] = value
            with self.assertRaises(c.ContractError): registry.project_synthetic(f)

    def test_duplicate_json_keys_invalid_and_nonfinite_json(self):
        for value in (b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff', b'{'):
            with self.subTest(value=value), self.assertRaises(c.ContractError): c.parse_json(value)

    def test_timestamp_is_explicit_utc_not_date_only(self):
        for value in ("2026-01-01Z", "2026-10-24T00:00:00+02:00", "2026-02-30T00:00:00Z"):
            with self.assertRaises(c.ContractError): c.timestamp(value)
