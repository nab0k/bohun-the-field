"""Synthetic OAuth lifecycle, budgets and actual Python↔JS integration over pipes."""
import contextlib
import copy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_cloud as harness
from collectors.source_registry.discovery.cloud import run
from collectors.source_registry.discovery.cloud.oauth import AccessProvider, EXCHANGE_BYTES
from collectors.source_registry.discovery.cloud.state import CloudHold
from collectors.source_registry.discovery.discovery import Worker
from collectors.source_registry.discovery.policy import Problem
from collectors.source_registry.discovery.radars import Budget, Response
from collectors.source_registry.discovery.store import Store


class ProviderTests(unittest.TestCase):
    def setUp(self):
        harness.CloudTests.setUp(self)  # block sockets and every real credential lookup too
        self.now = 1793000000
        self.sent = []
        self.events = []
        self.client = SimpleNamespace(held=True, poisoned=False, owner="synthetic", generation=1)
        self.reply = {"access_token": "SYNTHETIC_ACCESS", "expires_at": self.now + 3600,
                      "scope": "read", "credential_generation": 1}
        def request(scope):
            self.events.append("rpc"); self.sent.append(scope); return self.reply
        self.client.oauth_access = request
        self.http = SimpleNamespace(enabled=True,
            budget=Budget({"max_seconds": 600, "max_requests": 120, "max_bytes": 20_000_000}),
            reserve_api=lambda key: self.events.append("reserved"))
        self.provider = AccessProvider(self.client, self.http, clock=lambda: self.now)

    def test_cache_expiry_and_reservation_precedes_rpc(self):
        self.assertEqual(self.provider(), "SYNTHETIC_ACCESS")
        self.provider(); self.assertEqual(self.sent, ["read"])
        self.assertEqual(self.events, ["reserved", "rpc"])
        self.assertEqual(self.http.budget.bytes, EXCHANGE_BYTES)
        self.now += 3600; self.reply["expires_at"] = self.now + 3600
        self.provider(); self.assertEqual(len(self.sent), 2)

    def test_quota_failure_before_rpc_does_not_poison_known_state(self):
        self.http.reserve_api = lambda _: (_ for _ in ()).throw(Problem("QUOTA_BLOCKED"))
        with self.assertRaises(Problem): self.provider()
        self.assertEqual(self.sent, []); self.assertFalse(self.client.poisoned)

    def test_request_byte_time_caps_stop_before_rpc(self):
        for kind in ("requests", "bytes", "time"):
            with self.subTest(kind=kind):
                http = copy.copy(self.http)
                http.budget = Budget({"max_seconds": 600, "max_requests": 1, "max_bytes": EXCHANGE_BYTES})
                if kind == "requests": http.budget.requests = 1
                elif kind == "bytes": http.budget.bytes = 1
                else: http.budget.deadline = 0
                with self.assertRaises(Problem): AccessProvider(self.client, http, clock=lambda: self.now)()
        self.assertEqual(self.sent, [])

    def test_disabled_poisoned_or_changed_lease_never_returns_cached_access(self):
        self.provider(); self.client.held = False
        with self.assertRaises(CloudHold): self.provider()
        self.assertIsNone(self.provider.cached)

    def test_invalid_reply_and_unknown_scope_are_redacted_fatal(self):
        for override in ({"access_token": "bad\r\nvalue"}, {"scope": "write"},
                         {"expires_at": self.now}, {"expires_at": self.now + 32 * 86400},
                         {"refresh_token": "SYNTHETIC_PRIVATE_REFRESH"}):
            with self.subTest(override=override):
                self.client.poisoned = False; self.reply = {"access_token": "SYNTHETIC_ACCESS",
                    "expires_at": self.now + 3600, "scope": "read", **override}
                with self.assertRaises(CloudHold) as caught:
                    AccessProvider(self.client, self.http, clock=lambda: self.now)()
                self.assertNotIn("SYNTHETIC", str(caught.exception)); self.assertTrue(self.client.poisoned)

    def test_lost_response_is_not_retried_and_cache_cleared(self):
        def unknown(_):
            self.sent.append(1); raise TimeoutError("SYNTHETIC_PRIVATE_REFRESH")
        self.client.oauth_access = unknown
        for _ in range(2):
            with self.assertRaises(CloudHold): self.provider()
        self.assertEqual(self.sent, [1]); self.assertIsNone(self.provider.cached)

    def test_write_requires_write_scope(self):
        with self.assertRaises(CloudHold):
            AccessProvider(self.client, self.http, scope="read write", clock=lambda: self.now)()
        self.assertEqual(self.sent, ["read write"])

    def test_cached_read_access_is_not_reused_after_scope_change(self):
        self.provider(); self.provider.scope = "read write"
        with self.assertRaises(CloudHold): self.provider()
        self.assertEqual(self.sent, ["read", "read write"])


class IntegratedTests(unittest.TestCase):
    setUp = harness.CloudTests.setUp
    config = harness.CloudTests.config
    client = harness.CloudTests.client
    empty_snapshot = harness.CloudTests.empty_snapshot
    boot = harness.CloudTests.boot

    def transport(self):
        transport = harness.NodeTransport(run.code_hash(), oauth=True)
        self.addCleanup(transport.close); return transport

    def test_actual_js_rotation_cache_and_operational_checkpoint_excludes_secrets(self):
        transport = self.transport(); self.boot(transport)
        client = self.client(transport); client.acquire(); client.oauth_bootstrap()
        reservations = []
        http = SimpleNamespace(enabled=True,
            budget=Budget(harness.POLICY["limits"]), reserve_api=lambda k: reservations.append(k))
        provider = AccessProvider(client, http)
        self.assertEqual(provider(), "SYNTHETIC_ACCESS_1"); provider()
        self.assertEqual(len(reservations), 1)
        self.assertEqual(transport.exchange({"oauth_inspect": True})["calls"], 1)
        # A new ephemeral provider reuses DO cache, without a new upstream refresh.
        self.assertEqual(AccessProvider(client, http)(), "SYNTHETIC_ACCESS_1")
        self.assertEqual(transport.exchange({"oauth_inspect": True})["calls"], 1)
        transport.exchange({"advance_seconds": 3601})
        future = time.time() + 3601
        next_provider = AccessProvider(client, http, clock=lambda: future)
        self.assertEqual(next_provider(), "SYNTHETIC_ACCESS_2")
        self.assertEqual(transport.exchange({"oauth_inspect": True})["generation"], 2)
        raw = client.download()
        self.assertNotIn(b"SYNTHETIC_ACCESS", raw); self.assertNotIn(b"SYNTHETIC_REFRESH", raw)
        client.release()

    def test_missing_bootstrap_holds_without_shared_env_and_no_retry(self):
        transport = self.transport(); self.boot(transport); client = self.client(transport); client.acquire()
        http = SimpleNamespace(enabled=True, budget=Budget(harness.POLICY["limits"]), reserve_api=lambda _: None)
        provider = AccessProvider(client, http)
        with self.assertRaises(CloudHold): provider()
        before = len(transport.calls)
        with self.assertRaises(CloudHold): provider()
        self.assertEqual(len(transport.calls), before); self.assertEqual(self.env_reads, [])

    def test_cloud_requires_explicit_coordinator_mode_before_credential_lookup(self):
        document = self.config(); document["runtime"].update(live_inputs=True,
            providers=[{"id": "radar", "query_id": "synthetic", "enabled": True,
                        "type": "inoreader", "stream": "feed/keyword-monitoring-synthetic"}])
        with self.assertRaises(Problem): run.execute(document, mode="validate", token_provider=harness.blocked)
        document["runtime"]["oauth_mode"] = "coordinator"
        self.assertEqual(run.execute(document, mode="validate")["status"], "VALIDATED_NO_IO")
        document["runtime"]["oauth_env"] = {}
        with self.assertRaises(Problem): run.execute(document, mode="validate")
        self.assertEqual(self.env_reads, [])

    def test_optional_main_body_backfill_file_is_pinned(self):
        field = self.root / "synthetic-field"
        dep = field / "collectors/inoreader"; dep.mkdir(parents=True)
        for name in ("inoreader_source_manager.py", "sync.py", "content_contract.py"):
            (dep / name).write_text("# synthetic dependency\n")
        cf = field / "cloudflare/source-discovery"; cf.mkdir(parents=True)
        for name in ("state-core.mjs", "state-worker.mjs"): (cf / name).write_text("// synthetic\n")
        # Build a complete artificial package tree; no production file is touched.
        package = field / "collectors/source_registry/discovery"; cloud = package / "cloud"
        cloud.mkdir(parents=True)
        with patch.object(run, "FIELD", field), patch.object(run, "PACKAGE", package):
            # run.py pins its own real directory too, so assert only the dependency branch.
            # The actual package is outside FIELD in this fixture: replace Path(__file__) resolution.
            with patch.object(run, "__file__", str(cloud / "run.py")):
                self.assertNotIn("collectors/inoreader/body_backfill.py", run.code_pins())
                (dep / "body_backfill.py").write_text("# synthetic body backfill\n")
                self.assertIn("collectors/inoreader/body_backfill.py", run.code_pins())

    def test_cloud_worker_integration_reserves_before_actual_js_oauth(self):
        transport = self.transport(); self.boot(transport)
        seed_client = self.client(transport); seed_client.acquire(); seed_client.oauth_bootstrap(); seed_client.release()
        document = self.config(); document["runtime"].update(live_inputs=True, oauth_mode="coordinator",
            providers=[{"id": "radar", "query_id": "synthetic", "enabled": True,
                        "type": "inoreader", "stream": "feed/keyword-monitoring-synthetic"}])
        class RadarHTTP:
            def __init__(self, budget, reserve):
                self.budget, self.reserve_api, self.enabled = budget, reserve, True
            def request(self, url, **kwargs):
                if not url.startswith("https://www.inoreader.com/reader/api/0/stream/contents/"):
                    raise AssertionError("UNEXPECTED_FAKE_SOURCE")
                self.budget.request(); self.reserve_api("synthetic-source-request")
                if kwargs["headers"]["Authorization"] != "Bearer SYNTHETIC_ACCESS_1":
                    raise AssertionError("MISSING_AUTH")
                return Response(200, b'{"items":[]}', url, {})
        def factory(*args, **kwargs):
            return Worker(*args, **kwargs, http_factory=lambda b, r: RadarHTTP(b, r))
        result = run.execute(document, allow_live=True, transport=transport,
                             token_provider=lambda: harness.TOKEN, worker_factory=factory)
        self.assertEqual(result["status"], "CYCLE_FINISHED")
        self.assertEqual(transport.exchange({"oauth_inspect": True})["calls"], 1)
        rpc_index = next(i for i, c in enumerate(transport.calls) if c["url"].endswith("/oauth-access"))
        self.assertTrue(any(c["url"].endswith("/checkpoint") for c in transport.calls[:rpc_index]))
        self.assertEqual(self.env_reads, [])


if __name__ == "__main__":
    unittest.main()
