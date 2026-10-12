"""Offline checks for refresh-token rotation handoff. No Inoreader requests."""
import importlib.util
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors/inoreader"))
spec = importlib.util.spec_from_file_location("bohun_inoreader_sync", ROOT / "collectors/inoreader/sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class OAuthRotationTests(unittest.TestCase):
    def test_rotated_token_written_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "rotated-token"
            sync.save_rotated_token("sensitive-new-token", str(destination))
            self.assertEqual(destination.read_text(), "sensitive-new-token\n")
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)

    def test_no_token_leaked_to_stderr(self):
        with patch.dict(os.environ, {
            "INOREADER_CLIENT_ID": "id",
            "INOREADER_CLIENT_SECRET": "secret",
            "INOREADER_REFRESH_TOKEN": "old",
        }), patch.object(sync, "request_json", return_value={
            "access_token": "access-secret",
            "refresh_token": "new-secret",
        }):
            import contextlib
            import io
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(sync.access_token(), "access-secret")
            self.assertNotIn("new-secret", stderr.getvalue())
            self.assertNotIn("access-secret", stderr.getvalue())

    def test_explicit_local_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "token"
            with patch.dict(os.environ, {
                "INOREADER_CLIENT_ID": "id",
                "INOREADER_CLIENT_SECRET": "secret",
                "INOREADER_REFRESH_TOKEN": "old",
                "INOREADER_ROTATED_TOKEN_FILE": str(destination),
            }), patch.object(sync, "request_json", return_value={
                "access_token": "access",
                "refresh_token": "new",
            }):
                sync.access_token()
            self.assertEqual(destination.read_text(), "new\n")


if __name__ == "__main__":
    unittest.main()
