"""Offline tests for encrypted GitHub secret rotation. No network, no credentials."""
import base64
import importlib.util
import json
import os
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rotate_secret", ROOT / "scripts/inoreader-rotate-github-secret.py")
rotation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rotation)

ENV = {
    "INOREADER_SECRET_ADMIN_TOKEN": "fake-admin",
    "INOREADER_CLIENT_ID": "fake-client",
    "INOREADER_CLIENT_SECRET": "fake-client-secret",
    "INOREADER_REFRESH_TOKEN": "fake-old-refresh",
}
KEY = {"key": base64.b64encode(bytes([7]) * 32).decode(), "key_id": "fake-key-id"}


class SecretRotationTests(TestCase):
    def test_no_oauth_call_when_github_preflight_fails(self):
        with patch.dict(os.environ, ENV), patch.object(rotation, "request", return_value={}) as request:
            with self.assertRaisesRegex(RuntimeError, "GITHUB_SECRET_PUBLIC_KEY_UNAVAILABLE"):
                rotation.rotate()
            self.assertEqual(request.call_count, 1)

    def test_unchanged_refresh_token_does_not_update_secret(self):
        with patch.dict(os.environ, ENV), patch.object(rotation, "request", side_effect=[
            KEY, {"access_token": "fake-access", "refresh_token": "fake-old-refresh"}
        ]) as request, patch.object(rotation.urllib.request, "urlopen") as urlopen:
            rotation.rotate()
            self.assertEqual(request.call_count, 2)
            urlopen.assert_not_called()

    def test_rotated_secret_encrypted_before_github_update(self):
        class Response:
            status = 204
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False

        with patch.dict(os.environ, ENV), patch.object(rotation, "request", side_effect=[
            KEY, {"access_token": "fake-access", "refresh_token": "fake-new-refresh"}
        ]), patch.object(rotation.urllib.request, "urlopen", return_value=Response()) as urlopen:
            rotation.rotate()
            req = urlopen.call_args.args[0]
            self.assertEqual(req.get_method(), "PUT")
            self.assertNotIn(b"fake-new-refresh", req.data)
            body = json.loads(req.data)
            self.assertEqual(body["key_id"], "fake-key-id")
            self.assertNotEqual(base64.b64decode(body["encrypted_value"]), b"fake-new-refresh")


if __name__ == "__main__":
    main()
