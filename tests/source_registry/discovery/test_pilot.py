"""Bounded pilot behavior with the real Worker/Manager and artificial upstreams."""
import hashlib
import json
from unittest.mock import patch

from test_discovery import (Offline, Server, FakeHTTP, POLICY, POLICY_HASH, Problem, Budget, Store,
                            stamp, digest)
from collectors.source_registry.discovery.pilot import PilotAdmission, run_cycles
from collectors.source_registry.discovery.cloud.run import summary
from collectors.source_registry.discovery.cloud.shared import SharedHTTP, SharedQuota


class QuotaTransport(FakeHTTP):
    def __init__(self, server, budget, reserve):
        super().__init__(server, budget, lambda key: self.reserve_api(key))
        self.reserve_api = reserve
        self.enabled = True

    def request(self, url, **options):
        response = super().request(url, **options)
        response.headers.update({"X-Reader-Zone1-Limit": "100", "X-Reader-Zone1-Usage": "4",
                                 "X-Reader-Zone2-Limit": "100", "X-Reader-Zone2-Usage": "0",
                                 "X-Reader-Limits-Reset-After": "3600"})
        return response


class Pilots(Offline):
    def admission(self, store, profiles, **changes):
        value = {"version": 1, "stage": "single" if len(profiles) == 1 else "batch3",
                 "deployment_id": store.deployment, "code_sha256": "a" * 64,
                 "policy_sha256": POLICY_HASH, "expires_at": stamp(self.clock.now() + 600),
                 "max_attempts": len(profiles),
                 "sources": [{"feed_url": p["feed_url"], "group": p["assessment"]["group"]} for p in profiles]}
        value.update(changes)
        raw = json.dumps(value).encode()
        return PilotAdmission.load(raw, hashlib.sha256(raw).hexdigest(), deployment=store.deployment,
            code_hash="a" * 64, policy_hash=POLICY_HASH, now=self.clock.now())

    def pilot_worker(self, store, server, admission, **limits):
        worker = self.worker(store, server, providers=[], pilot_proofs={"single": False, "batch3": False})
        worker.pilot_admission = admission
        worker.shared_budget = Budget({**POLICY["limits"], **limits}, monotonic=self.clock.monotonic)
        return worker

    def test_first_single_uses_existing_writer_without_faking_pilot_proofs(self):
        store, server = self.state(), Server()
        profile = self.profile(store)
        worker = self.pilot_worker(store, server, self.admission(store, [profile]))
        reports, complete = run_cycles(worker, worker.pilot_admission, POLICY_HASH)
        self.assertTrue(complete)
        self.assertEqual([p[0] for p in server.posts], ["quickadd", "edit"])
        self.assertEqual(reports[0]["new_subscriptions"], 1)
        self.assertEqual(worker.config["pilot_proofs"], {"single": False, "batch3": False})
        self.assertEqual(store.profiles()[0]["delivery"]["state"], "WAITING_DELIVERY")

    def test_batch_three_and_replay_share_store_and_make_no_duplicate_add(self):
        store, server = self.state(), Server()
        profiles = [self.profile(store, f"https://publisher-{i}.example/feed") for i in range(3)]
        worker = self.pilot_worker(store, server, self.admission(store, profiles))
        reports, complete = run_cycles(worker, worker.pilot_admission, POLICY_HASH)
        self.assertTrue(complete); self.assertEqual(len(reports), 3)
        self.assertEqual([r["new_subscriptions"] for r in reports], [1, 1, 1])
        self.assertEqual(len([p for p in server.posts if p[0] == "quickadd"]), 3)
        def write_attempts():
            return store.db.execute("SELECT count(*) FROM attempt_ledger WHERE kind <> 'discovery_api'").fetchone()[0]
        before = write_attempts()
        replay, complete = run_cycles(worker, worker.pilot_admission, POLICY_HASH)
        self.assertTrue(complete); self.assertEqual(replay[0]["new_subscriptions"], 0)
        self.assertEqual(write_attempts(), before)
        self.assertEqual(len(server.posts), 6)

    def reviewed_batch(self, store):
        feeds, sources = {}, []
        for i, group in enumerate(["Media / intelligence", "Media / intelligence",
                                   "Governments / procurement / regulation"]):
            url = f"https://reviewed-{i}.example/feed"
            feed = self.feed(url)
            feed["title"] = "Editorial briefings"
            for number, entry in enumerate(feed["entries"]):
                entry["title"] = f"Editorial briefing {number}"
            feed["assessment"] = {"decision": "REVIEW", "group": None,
                                  "rule_ids": ["gate:domain-signal-ratio", "route:ambiguous-or-unknown"]}
            store.ingest("tool", "fixture", [{"item_id": digest(url), "feed_url": url}], {}, self.clock.now())
            store.validate(digest(url), [feed], self.clock.now())
            feeds[url] = feed
            sources.append({"feed_url": url, "group": group})
        return feeds, self.admission(store, store.profiles(), stage="batch3_reviewed", sources=sources)

    def test_reviewed_batch_routes_exact_three_and_replay_does_not_add(self):
        store, server = self.state(), Server()
        feeds, admission = self.reviewed_batch(store)
        unlisted = self.profile(store, "https://unlisted.example/feed")
        # The ordinary bridge still refuses REVIEW even if continuous proof is configured.
        with self.assertRaisesRegex(Problem, "ADMISSION_REQUIRED"):
            self.bridge(store, server).connect(next(p for p in store.profiles() if p["feed_url"] in feeds))
        worker = self.pilot_worker(store, server, admission)
        with patch("collectors.source_registry.discovery.discovery.discover",
                   side_effect=lambda url, *_: [feeds[url]]) as discoverer:
            reports, complete = run_cycles(worker, admission, POLICY_HASH)
            self.assertTrue(complete, reports)
            self.assertCountEqual([call.args[0] for call in discoverer.call_args_list], feeds)
            self.assertEqual([r["new_subscriptions"] for r in reports], [1, 1, 1])
            replay, complete = run_cycles(worker, admission, POLICY_HASH)
        self.assertTrue(complete, replay)
        self.assertEqual(replay[0]["new_subscriptions"], 0)
        self.assertEqual(len(server.posts), 6)
        self.assertEqual([method for method, _ in server.requests[:10]], ["GET"] + ["POST", "POST", "GET"] * 3)
        self.assertNotIn("feed/" + unlisted["feed_url"], server.subs)
        folders = {p["feed_url"]: p["connection"]["folder"] for p in store.profiles()
                   if p["feed_url"] in feeds}
        self.assertEqual(list(folders.values()).count("BOHUN_MEDIA"), 2)
        self.assertEqual(folders["https://reviewed-2.example/feed"], "BOHUN_GOV")
        for row in store.profiles():
            if row["feed_url"] in feeds:
                self.assertEqual(row["assessment"]["decision"], "REVIEW")
                self.assertIsNone(row["assessment"]["group"])
                self.assertEqual(row["connection"]["pilot_sha256"], admission.sha256)
                self.assertEqual(row["delivery"]["state"], "WAITING_DELIVERY")
        self.assertFalse(worker.config["pilot_proofs"]["batch3"])

    def test_reviewed_batch_holds_all_writes_when_last_feed_changes(self):
        for fault in ("identity", "endpoint", "freshness", "duplicate", "another_manifest"):
            with self.subTest(fault=fault):
                store, server = self.state(name=fault), Server()
                feeds, admission = self.reviewed_batch(store)
                last = list(feeds.values())[-1]
                if fault == "identity":
                    last["language"] = "es"
                elif fault == "endpoint":
                    last["feed_url"] += "/changed"
                elif fault == "freshness":
                    for entry in last["entries"]:
                        entry["published_epoch"] = 1
                elif fault == "duplicate":
                    last["entries"] = list(feeds.values())[0]["entries"]
                else:
                    profile = next(p for p in store.profiles() if p["feed_url"] == last["feed_url"])
                    profile["lifecycle"] = "CONNECTED"
                    profile["connection"] = {"pilot_sha256": "b" * 64}
                    store.put_profile(profile)
                worker = self.pilot_worker(store, server, admission)
                with patch("collectors.source_registry.discovery.discovery.discover",
                           side_effect=lambda url, *_: [feeds[url]]):
                    reports, complete = run_cycles(worker, admission, POLICY_HASH)
                self.assertFalse(complete, reports)
                self.assertEqual(server.requests, [])

    def test_batch_uncertain_first_response_stops_without_retry(self):
        store, server = self.state(), Server()
        profiles = [self.profile(store, f"https://publisher-{i}.example/feed") for i in range(3)]
        worker = self.pilot_worker(store, server, self.admission(store, profiles))
        server.mode = "timeout_after_add"
        reports, complete = run_cycles(worker, worker.pilot_admission, POLICY_HASH)
        self.assertFalse(complete); self.assertEqual(len(reports), 1)
        self.assertEqual(len(server.posts), 1); self.assertTrue(store.unresolved())
        run_cycles(worker, worker.pilot_admission, POLICY_HASH)
        self.assertEqual(len(server.posts), 1)

    def test_batch_checks_quota_before_writes_and_verifies_each_connection(self):
        for used in (2, 3):
            with self.subTest(used=used):
                store, server = self.state(name=f"batch-{used}"), Server()
                profiles = [self.profile(store, f"https://batch-{i}.example/feed") for i in range(3)]
                seed = QuotaTransport(server, Budget(POLICY["limits"]), lambda _: None)
                headers = seed.request("https://www.inoreader.com/reader/api/0/subscription/list").headers
                server.requests.clear()
                collector = SharedQuota(store, "collector", clock=self.clock.now)
                collector.reserve_attempt("1")
                collector.observe(headers)
                quota = SharedQuota(store, "discovery", clock=self.clock.now)
                for _ in range(used):
                    quota.reserve_attempt("1")
                    quota.observe(headers)
                worker = self.pilot_worker(store, server, self.admission(store, profiles))
                worker.http_factory = lambda budget, reserve: QuotaTransport(server, budget, reserve)
                worker.http_wrapper = lambda http: SharedHTTP(http, quota)
                reports, complete = run_cycles(worker, worker.pilot_admission, POLICY_HASH, quota=quota)
                if used == 2:
                    self.assertTrue(complete)
                    self.assertEqual([method for method, _ in server.requests], ["GET"] + ["POST", "POST", "GET"] * 3)
                    self.assertEqual(worker.pilot_admission.connected_count(store.profiles()), 3)
                else:
                    self.assertFalse(complete)
                    self.assertEqual(reports[0]["errors"], ["SHARED_PILOT_QUOTA_INSUFFICIENT"])
                    self.assertEqual(server.requests, [])
                self.assertEqual(store.unresolved(), [])

    def test_batch_checks_all_three_before_adding_the_first_source(self):
        for rejected in ("review", "already_subscribed", "existing_alias", "destination_unavailable"):
            with self.subTest(rejected=rejected):
                store, server = self.state(name=rejected), Server()
                profiles = [self.profile(store, f"https://batch-{i}.example/feed") for i in range(3)]
                admission = self.admission(store, profiles)
                last = store.profiles()[-1]
                if rejected == "review":
                    last["assessment"]["decision"] = "REVIEW"
                    store.put_profile(last)
                elif rejected in ("already_subscribed", "existing_alias"):
                    stream = "feed/" + last["feed_url"]
                    if rejected == "existing_alias":
                        stream += "/alternate"
                    server.subs[stream] = {"id": stream, "categories": []}
                worker = self.pilot_worker(store, server, admission)
                if rejected == "destination_unavailable":
                    def delivery(profile):
                        if profile["feed_url"] == last["feed_url"]:
                            raise Problem("DELIVERY_READ_FAILED")
                        return server.delivery(profile)
                    worker.delivery_reader = delivery
                _, complete = run_cycles(worker, admission, POLICY_HASH)
                self.assertFalse(complete)
                self.assertEqual(server.posts, [])

    def test_review_and_unlisted_sources_are_never_overridden(self):
        store, server = self.state(), Server()
        wanted = self.profile(store)
        other = self.profile(store, "https://unlisted.example/feed")
        admission = self.admission(store, [wanted])
        wanted["assessment"]["decision"] = "REVIEW"; store.put_profile(wanted)
        worker = self.pilot_worker(store, server, admission)
        reports, complete = run_cycles(worker, admission, POLICY_HASH)
        self.assertFalse(complete); self.assertEqual(server.posts, [])
        self.assertEqual(reports[0]["new_subscriptions"], 0)
        bridge = self.bridge(store, server); bridge.pilot_admission = admission
        for row in (wanted, other):
            with self.assertRaisesRegex(Problem, "PILOT_SOURCE_NOT_ADMITTED"): bridge.connect(row)
        self.assertEqual(server.requests, [])

    def test_manifest_shape_pins_duplicates_expiry_and_cap_reject(self):
        store = self.state(); profile = self.profile(store)
        for change in ({"max_attempts": 2}, {"version": True}, {"stage": "all"},
                       {"deployment_id": "other"}, {"code_sha256": "b" * 64},
                       {"policy_sha256": "b" * 64}, {"expires_at": stamp(self.clock.now())},
                       {"expires_at": stamp(self.clock.now() + 86401)},
                       {"unknown": "do not accept"},
                       {"stage": "batch3", "max_attempts": 3, "sources": [
                           {"feed_url": profile["feed_url"], "group": profile["assessment"]["group"]}] * 3}):
            with self.subTest(change=change), self.assertRaises(Problem): self.admission(store, [profile], **change)
        with self.assertRaisesRegex(Problem, "PILOT_MANIFEST_HASH"):
            PilotAdmission.load(b"{}", "0" * 64, deployment=store.deployment,
                code_hash="a" * 64, policy_hash=POLICY_HASH, now=self.clock.now())

    def test_expired_manifest_stops_before_source_or_token_io(self):
        store, server = self.state(), Server(); p = self.profile(store)
        admission = self.admission(store, [p]); self.clock.advance(601)
        worker = self.pilot_worker(store, server, admission)
        with self.assertRaisesRegex(Problem, "PILOT_EXPIRED"): run_cycles(worker, admission, POLICY_HASH)
        self.assertEqual(server.requests, [])

    def test_manifest_attempt_is_durable_across_restart_even_without_send(self):
        store = self.state(); profile = self.profile(store); admission = self.admission(store, [profile])
        admission.reserve(profile, store, "first", self.clock.now())
        root, deployment = store.root, store.deployment
        store.close()
        with Store(root, deployment) as resumed:
            with self.assertRaisesRegex(Problem, "ATTEMPT_ALREADY_RESERVED"):
                admission.reserve(profile, resumed, "second", self.clock.now() + 1)

    def test_day_five_cap_applies_to_pilot_too(self):
        store, server = self.state(), Server(); profile = self.profile(store)
        for n in range(5):
            store.reserve("old" + str(n), "run" + str(n), "quickadd", self.clock.now(), day_limit=5, cycle_limit=1)
        worker = self.pilot_worker(store, server, self.admission(store, [profile]))
        _, complete = run_cycles(worker, worker.pilot_admission, POLICY_HASH)
        self.assertFalse(complete); self.assertEqual(server.posts, [])

    def test_batch_does_not_multiply_request_or_deadline_budget(self):
        store, server = self.state(), Server()
        profiles = [self.profile(store, f"https://publisher-{i}.example/feed") for i in range(3)]
        worker = self.pilot_worker(store, server, self.admission(store, profiles), max_requests=7)
        _, complete = run_cycles(worker, worker.pilot_admission, POLICY_HASH)
        self.assertFalse(complete); self.assertLessEqual(worker.shared_budget.requests, 7)
        self.assertLess(len([p for p in server.posts if p[0] == "quickadd"]), 3)
        self.clock.advance(601)
        with self.assertRaises(Problem): worker.shared_budget.remaining()

    def test_dns_validation_failure_is_partial_but_review_is_not_network_failure(self):
        store, server = self.state(), Server()
        store.ingest("tool", "q", [{"item_id": digest("dns"), "feed_url": "https://dns.example/feed"}], {}, self.clock.now())
        worker = self.worker(store, server, providers=[])
        worker.http_factory = lambda *_: type("Unavailable", (), {
            "request": lambda *_a, **_k: (_ for _ in ()).throw(Problem("DNS_UNAVAILABLE"))})()
        report = worker.cycle()
        self.assertEqual(report["result"], "PARTIAL")
        self.assertEqual(summary(report, False)["status"], "CYCLE_PARTIAL")
        self.assertEqual(summary({"providers": {}, "errors": [], "validation": [
            {"decisions": ["REVIEW"]}], "new_subscriptions": 0}, False)["status"], "CYCLE_FINISHED")
