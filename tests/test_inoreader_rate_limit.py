import importlib.util
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("bohun_inoreader_sync", "collectors/inoreader/sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, *_):
        return b'{"subscriptions": []}'


class RateLimitTests(unittest.TestCase):
    def test_retries_then_succeeds(self):
        error = urllib.error.HTTPError("https://example.test", 429, "limited", {"Retry-After": "1"}, None)
        with patch.object(sync.urllib.request, "urlopen", side_effect=[error, Response()]) as urlopen, patch.object(sync.time, "sleep") as sleep:
            result = sync.request_json("https://example.test")
        self.assertEqual(result, {"subscriptions": []})
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_bounded_retries(self):
        error = urllib.error.HTTPError("https://example.test", 429, "limited", {}, None)
        with patch.object(sync.urllib.request, "urlopen", side_effect=[error] * 4) as urlopen, patch.object(sync.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "status=429"):
                sync.request_json("https://example.test")
        self.assertEqual(urlopen.call_count, 4)
        self.assertEqual(sleep.call_count, 3)


if __name__ == "__main__":
    unittest.main()
