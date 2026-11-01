"""Bounded pilot behavior with the real Worker/Manager and artificial upstreams."""
import hashlib
import json

from test_discovery import (Offline, Server, POLICY, POLICY_HASH, Problem, Budget, Store,
                            stamp, digest)
from collectors.source_registry.discovery.pilot import PilotAdmission, run_cycles
from collectors.source_registry.discovery.cloud.run import summary


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
