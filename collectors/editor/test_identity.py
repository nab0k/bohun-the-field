"""Offline regression tests for pinning candidate event metadata."""
import unittest

from collectors.editor.identity import candidate_identity_hash


class CandidateIdentityTests(unittest.TestCase):
    def setUp(self):
        self.kw = dict(material_id="syn-1", material_type="synthesis", version=1,
                       author_model_id="model-A", event_ids=("EV-1",),
                       event_fingerprint="event-fingerprint")

    def digest(self, **changes):
        return candidate_identity_hash(**{**self.kw, **changes})

    def test_valid_digest_is_sha256(self):
        self.assertEqual(len(self.digest()), 64)

    def test_same_candidate_has_stable_digest(self):
        self.assertEqual(self.digest(), self.digest())

    def test_change_material_id_invalidates(self):
        self.assertNotEqual(self.digest(), self.digest(material_id="syn-2"))

    def test_change_type_invalidates(self):
        self.assertNotEqual(self.digest(), self.digest(material_type="signal"))

    def test_change_version_invalidates(self):
        self.assertNotEqual(self.digest(), self.digest(version=2))

    def test_change_author_model_invalidates(self):
        self.assertNotEqual(self.digest(), self.digest(author_model_id="model-B"))

    def test_change_event_ids_invalidates(self):
        self.assertNotEqual(self.digest(), self.digest(event_ids=("EV-2",)))

    def test_change_event_fingerprint_invalidates(self):
        self.assertNotEqual(self.digest(), self.digest(event_fingerprint="changed"))

    def test_invalid_nested_event_id_fails_closed(self):
        self.assertEqual(self.digest(event_ids=(["EV-1"],)), "")

    def test_duplicate_event_ids_fail_closed(self):
        self.assertEqual(self.digest(event_ids=("EV-1", "EV-1")), "")

    def test_bool_version_is_not_valid_integer(self):
        self.assertEqual(self.digest(version=True), "")

    def test_blank_author_model_fails_closed(self):
        self.assertEqual(self.digest(author_model_id=""), "")


if __name__ == "__main__":
    unittest.main()
