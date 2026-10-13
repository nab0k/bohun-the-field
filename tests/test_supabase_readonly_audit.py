import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "supabase-readonly-audit.py"
SPEC = importlib.util.spec_from_file_location("supabase_readonly_audit", SCRIPT)
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class SupabaseReadonlyAuditTests(unittest.TestCase):
    def test_missing_secret_is_explicitly_blocked(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            report = AUDIT.audit()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["mode"], "READ_ONLY")
        self.assertFalse(report["record_content_included"])

    def test_static_metadata_queries_are_select_only(self):
        forbidden = ("insert", "update", "delete", "alter", "drop", "create", "truncate", "grant", "revoke")
        for query in AUDIT.METADATA_QUERIES.values():
            normalized = " ".join(query.lower().split())
            self.assertTrue(normalized.startswith("select "))
            for keyword in forbidden:
                self.assertNotRegex(normalized, rf"\b{keyword}\b")

    def test_view_literals_and_comments_are_redacted(self):
        result = AUDIT.sanitize_view_definition("SELECT * FROM t WHERE token = 'secret' -- private")
        self.assertNotIn("secret", result["sanitized_definition"])
        self.assertNotIn("private", result["sanitized_definition"])
        self.assertRegex(result["definition_sha256"], r"^[a-f0-9]{64}$")

    def test_custom_role_names_are_hashed(self):
        self.assertEqual(AUDIT.safe_grantee("anon"), "anon")
        self.assertTrue(AUDIT.safe_grantee("internal_reader").startswith("role-sha256:"))


if __name__ == "__main__":
    unittest.main()
