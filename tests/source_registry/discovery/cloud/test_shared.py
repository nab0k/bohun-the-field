"""Shared quota, real coordinator protocol and collector admission; entirely offline."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import test_cloud as harness
from collectors.inoreader import sync
from collectors.inoreader.shared_runtime import CollectorSession, NoRedirect
from collectors.source_registry.discovery.cloud.shared import SharedQuota, SharedHTTP, KEY, api_zone
from collectors.source_registry.discovery.cloud.state import CloudHold
from collectors.source_registry.discovery.cloud import run
from collectors.source_registry.discovery.policy import Problem
from collectors.source_registry.discovery.radars import API, Budget, Response
from collectors.source_registry.discovery.store import CheckpointError, Store


def headers(used=1, *, limit=100, reset=3600, write_used=0):
    return {"X-Reader-Zone1-Limit": str(limit), "X-Reader-Zone1-Usage": str(used),
            "X-Reader-Zone2-Limit": "100", "X-Reader-Zone2-Usage": str(write_used),
            "X-Reader-Limits-Reset-After": str(reset)}


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name).resolve() / "state", "synthetic", bootstrap=True)
        self.addCleanup(self.store.close)
        self.now = 1793000000

    def quota(self, consumer="collector", **kwargs):
        return SharedQuota(self.store, consumer, clock=lambda: self.now, **kwargs)

    def seed(self, used=1, **kwargs):
        quota = self.quota(); quota.reserve_attempt("1"); quota.observe(headers(used, **kwargs))

    def test_discovery_cannot_probe_unknown_window(self):
        with self.assertRaisesRegex(Problem, "COLLECTOR_PROBE_REQUIRED"):
            self.quota("discovery").reserve_attempt("1")
        self.assertIsNone(self.store.meta(KEY))

    def test_one_collector_probe_persists_before_send_and_is_not_repeated(self):
        events = []
        self.store.checkpoint = lambda store: events.append(json.loads(store.meta(KEY)))
        self.quota().reserve_attempt("1")
        self.assertEqual(events[-1]["zones"]["1"]["charged"], 1)
        with self.assertRaisesRegex(Problem, "UNKNOWN"):
            self.quota().reserve_attempt("1")

    def test_discovery_preserves_collector_reserve_and_collector_can_continue(self):
        self.seed(79)
        discovery = self.quota("discovery")
        discovery.reserve_attempt("1"); discovery.observe(headers(79))
        with self.assertRaisesRegex(Problem, "COLLECTOR_RESERVE"):
            discovery.reserve_attempt("1")
        collector = self.quota(); collector.reserve_attempt("1")
        self.assertEqual(collector.read()["zones"]["1"]["charged"], 81)

    def test_global_hard_cap_applies_to_both_consumers_and_survives_new_client(self):
        self.seed(99)
        quota = self.quota(); quota.reserve_attempt("1"); quota.observe(headers(99))
        with self.assertRaisesRegex(Problem, "EXHAUSTED"):
            self.quota().reserve_attempt("1")

    def test_headers_never_refund_reserved_or_external_usage(self):
        self.seed(40)
        quota = self.quota(); quota.reserve_attempt("1"); quota.observe(headers(2))
        self.assertEqual(quota.read()["zones"]["1"]["charged"], 41)
        quota.reserve_attempt("1"); quota.observe(headers(75))
        self.assertEqual(quota.read()["zones"]["1"]["charged"], 75)

    def test_discovery_daily_cap_is_shared_across_ephemeral_clients(self):
        self.seed()
        for _ in range(2):
            quota = self.quota("discovery", discovery_day_cap=2)
            quota.reserve_attempt("1"); quota.observe(headers())
        with self.assertRaisesRegex(Problem, "DAY_CAP"):
            self.quota("discovery", discovery_day_cap=2).reserve_attempt("1")

    def test_expired_window_requires_collector_to_learn_new_server_usage(self):
        self.seed(90)
        self.now += 3601
        with self.assertRaisesRegex(Problem, "COLLECTOR_PROBE_REQUIRED"):
            self.quota("discovery").reserve_attempt("1")
        quota = self.quota(); quota.reserve_attempt("1"); quota.observe(headers(25))
        self.assertEqual(quota.read()["zones"]["1"]["charged"], 25)

    def test_missing_headers_or_unknown_attempt_do_not_open_another_probe(self):
        quota = self.quota(); quota.reserve_attempt("1")
        with self.assertRaisesRegex(Problem, "HEADERS_REQUIRED"):
            quota.observe({})
        with self.assertRaisesRegex(Problem, "UNKNOWN"):
            self.quota().reserve_attempt("1")

    def test_lost_checkpoint_ack_prevents_the_external_request(self):
        def lost(_):
            raise TimeoutError("synthetic lost storage acknowledgement")
        self.store.checkpoint = lost
        with self.assertRaises(CheckpointError):
            self.quota().reserve_attempt("1")
        self.assertTrue(self.store.poisoned)

    def test_lower_server_limit_tightens_admission_and_upgrade_does_not_expand_100(self):
        self.seed(15, limit=20)
        quota = self.quota(); quota.reserve_attempt("1"); quota.observe(headers(16, limit=1000))
        self.assertEqual(quota.read()["zones"]["1"]["limit"], 20)
        self.assertEqual(quota.read()["zones"]["2"]["limit"], 100)

    def test_unsafe_methods_and_destinations_are_rejected(self):
        self.assertEqual(api_zone(API + "subscription/list", "GET"), "1")
        self.assertEqual(api_zone(API + "subscription/edit", "POST"), "2")
        for url, method in [("https://other.example/", "GET"), (API + "subscription/edit", "GET"),
                            (API + "anything", "POST"), (API + "subscription/list#fragment", "GET")]:
            with self.subTest(url=url), self.assertRaises(Problem): api_zone(url, method)

    def test_shared_http_keeps_existing_budget_and_charges_before_upstream(self):
        self.seed()
        quota = self.quota("discovery")
        events = []
        class HTTP:
            enabled = True
            budget = Budget({"max_seconds": 60, "max_requests": 3, "max_bytes": 1000})
            reserve_api = staticmethod(lambda key: events.append("existing-ledger"))
            def request(inner, url, **kwargs):
                inner.reserve_api("synthetic-attempt")
                self.assertEqual(quota.read()["zones"]["1"]["charged"], 2)
                events.append("upstream")
                return Response(200, b'{}', url, headers())
        wrapped = SharedHTTP(HTTP(), quota)
        with self.assertRaisesRegex(Problem, "ADMISSION_REQUIRED"):
            wrapped.request(API + "subscription/list")
        wrapped.request(API + "subscription/list", api=True)
        self.assertEqual(events, ["existing-ledger", "upstream"])

    def test_write_zone_is_learned_by_read_probe_but_never_before(self):
        with self.assertRaises(Problem): self.quota("discovery").reserve_attempt("2")
        self.seed(write_used=80)
        with self.assertRaisesRegex(Problem, "COLLECTOR_RESERVE"):
            self.quota("discovery").reserve_attempt("2")


class SharedCollectorTests(unittest.TestCase):
    setUp = harness.CloudTests.setUp
    config = harness.CloudTests.config
    client = harness.CloudTests.client
    empty_snapshot = harness.CloudTests.empty_snapshot
    boot = harness.CloudTests.boot

    def transport(self):
        transport = harness.NodeTransport(run.code_hash(), oauth=True)
        self.addCleanup(transport.close)
        self.boot(transport)
        client = self.client(transport); client.acquire(); client.oauth_bootstrap(); client.release()
        return transport

    def document(self, name="collector"):
        doc = self.config(name)
        doc["runtime"].update(oauth_mode="coordinator", api_quota_mode="shared")
        return doc

    def test_collector_and_discovery_share_oauth_quota_and_exclusive_lease(self):
        transport = self.transport()
        with CollectorSession(self.document(), token_provider=lambda: harness.TOKEN, transport=transport) as session:
            self.assertEqual(session.access_token(), "SYNTHETIC_ACCESS_1")
            session.before_request(API + "subscription/list", "GET"); session.observe(headers(35))
            with self.assertRaises(CloudHold): self.client(transport).acquire()
        with self.assertRaisesRegex(Problem, "COLLECTOR_SHARED_LEASE"):
            session.access_token()
        # A fresh ephemeral process sees the same Store budget and DO token cache.
        with CollectorSession(self.document("collector-two"), token_provider=lambda: harness.TOKEN, transport=transport) as session:
            self.assertEqual(session.quota.read()["zones"]["1"]["charged"], 35)
            self.assertEqual(session.access_token(), "SYNTHETIC_ACCESS_1")
            self.assertEqual(transport.exchange({"oauth_inspect": True})["calls"], 1)
        self.assertEqual(self.env_reads, [])

    def test_shared_collector_passes_existing_sync_pipeline_without_legacy_refresh(self):
        transport = self.transport()
        data = [{"subscriptions": [{"id": "feed/https://publisher.example/feed", "categories": [{"label": "BOHUN_MEDIA"}]}]}, {"items": []}]
        class Reply(io.BytesIO):
            headers = headers(3)
        with CollectorSession(self.document(), token_provider=lambda: harness.TOKEN, transport=transport) as session:
            calls = []
            def opened(req, **kwargs):
                calls.append(req.full_url)
                return Reply(json.dumps(data.pop(0)).encode())
            with patch.object(session, "open_request", side_effect=opened), \
                 patch.object(sync, "access_token", side_effect=harness.blocked), \
                 patch.object(sync, "import_items", side_effect=harness.blocked), contextlib.redirect_stdout(io.StringIO()):
                sync.main(["--dry-run", "--max-pages", "1"], services=session)
            self.assertEqual(len(calls), 2)
            self.assertEqual(session.quota.attempts, 2)

    def test_optional_shared_mode_requires_explicit_configuration_without_env_fallback(self):
        doc = self.config()
        with self.assertRaisesRegex(Problem, "COLLECTOR_SHARED_NOT_ENABLED"):
            CollectorSession(doc, token_provider=harness.blocked)
        self.assertEqual(self.env_reads, [])

    def test_manual_pilot_cap_stops_before_an_extra_api_call(self):
        transport = self.transport()
        with CollectorSession(self.document(), token_provider=lambda: harness.TOKEN,
                              transport=transport, api_attempt_cap=1) as session:
            session.before_request(API + "subscription/list", "GET"); session.observe(headers())
            with self.assertRaisesRegex(Problem, "ATTEMPT_GUARD"):
                session.before_request(API + "subscription/list", "GET")
            self.assertEqual(session.quota.attempts, 1)

    def test_shared_request_does_not_retry_429_or_follow_redirects(self):
        import urllib.error
        transport = self.transport()
        with CollectorSession(self.document(), token_provider=lambda: harness.TOKEN, transport=transport) as session:
            error = urllib.error.HTTPError(API + "subscription/list", 429, "SYNTHETIC", headers(100), None)
            with patch.object(session, "open_request", side_effect=error) as opened:
                with self.assertRaisesRegex(RuntimeError, "status=429"):
                    session.api("subscription/list")
                self.assertEqual(opened.call_count, 1)
            self.assertIsNone(NoRedirect().redirect_request(None, None, None, None, None, None))
