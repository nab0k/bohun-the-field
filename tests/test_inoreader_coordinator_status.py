"""Coordinator status probe: one POST /v1/status, only safe fields printed (no network)."""
import importlib.util
import io
import json
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("probe", Path(__file__).resolve().parents[1] / "scripts/inoreader-coordinator-status.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
DOC = {"state_endpoint": "https://bohun-source-discovery-state.example.workers.dev", "code_sha256": "a" * 64,
       "policy_sha256": "b" * 64, "runtime": {"deployment_id": "dep-1"}}
REPLY = {"version": 1, "deployment": "dep-1", "revision": 7, "generation": 13, "leased": True,
         "oauth": {"credential": {"generation": 5, "status": "REFRESHING", "expires_at": 1, "scope": "read",
                                  "cached_access_bytes": 40, "access": "SECRET-ACCESS", "refresh": "SECRET-REFRESH"},
                   "diagnostic": {"generation": 5, "stage": "OAUTH_NETWORK_UNKNOWN", "http_status": None,
                                  "upstream_may_have_started": 1},
                   "recovery": {"terminated_run_id": "x", "token": "SECRET-TOKEN"}}}


class Response(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Probe(unittest.TestCase):
    def test_one_post_and_safe_fields_only(self):
        seen = []

        def opener(req, timeout):
            seen.append((req.full_url, req.get_method(), req.data, dict(req.header_items())))
            return Response(json.dumps(REPLY).encode())
        status, data = probe.request_status(DOC, "state-token", opener=opener)
        out = json.dumps(probe.safe(status, data))
        self.assertEqual(len(seen), 1)
        self.assertEqual((seen[0][0], seen[0][1], seen[0][2]), (DOC["state_endpoint"] + "/v1/status", "POST", b"{}"))
        self.assertEqual(seen[0][3]["X-deployment"], "dep-1")
        for secret in ("SECRET", "state-token", "workers.dev", "dep-1"):
            self.assertNotIn(secret, out)
        s = probe.safe(status, data)
        self.assertEqual((s["leased"], s["credential"]["status"], s["diagnostic"]["stage"]),
                         (True, "REFRESHING", "OAUTH_NETWORK_UNKNOWN"))

    def test_foreign_endpoint_refused(self):
        with self.assertRaises(ValueError):
            probe.request_status({**DOC, "state_endpoint": "https://evil.example"}, "t", opener=None)


if __name__ == "__main__":
    unittest.main()
