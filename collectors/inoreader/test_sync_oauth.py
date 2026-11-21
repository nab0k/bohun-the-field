"""Offline OAuth tests: never call Inoreader or print credentials."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name("sync.py")
spec = importlib.util.spec_from_file_location("bohun_inoreader_sync", MODULE)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

ENV = {
    "INOREADER_CLIENT_ID": "test-id",
    "INOREADER_CLIENT_SECRET": "test-secret",
    "INOREADER_REFRESH_TOKEN": "old-token",
}


class OAuthTests(unittest.TestCase):
    def test_normal_refresh(self):
        with patch.dict(os.environ, ENV), patch.object(sync, "request_json", return_value={"access_token": "access"}):
            self.assertEqual(sync.access_token(), "access")

    def test_rotated_refresh_stops(self):
        with patch.dict(os.environ, ENV), patch.object(sync, "request_json", return_value={"access_token": "access", "refresh_token": "new-token"}):
            with self.assertRaises(sync.OAuthTokenRotated) as ctx:
                sync.access_token()
            self.assertNotIn("new-token", str(ctx.exception))

    def test_unchanged_refresh_allowed(self):
        with patch.dict(os.environ, ENV), patch.object(sync, "request_json", return_value={"access_token": "access", "refresh_token": "old-token"}):
            self.assertEqual(sync.access_token(), "access")

    def test_missing_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "Missing Inoreader OAuth"):
                sync.access_token()

    def test_no_access_token(self):
        with patch.dict(os.environ, ENV), patch.object(sync, "request_json", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "did not return access_token"):
                sync.access_token()


if __name__ == "__main__":
    unittest.main()
