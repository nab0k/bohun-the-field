"""Offline Python↔actual JS protocol tests over pipes; no network, credentials or DB service."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch
import urllib.request

FIELD = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(FIELD))
from collectors.source_registry.discovery.cloud import run
from collectors.source_registry.discovery.cloud.state import CloudHold, StateClient, MAX_CALLS, MAX_TRANSFER, MAX_SNAPSHOT
from collectors.source_registry.discovery.policy import Problem, load_policy, digest
from collectors.source_registry.discovery.radars import Response
from collectors.source_registry.discovery.store import CheckpointError, Store

POLICY_PATH = FIELD / "collectors/source_registry/discovery/policy.proposed.json"
POLICY, POLICY_HASH = load_policy(POLICY_PATH)
TOKEN = "SYNTHETIC_" + "t" * 64
ENDPOINT = "https://bohun-source-discovery-state.synthetic.workers.dev"
DEPLOYMENT = "synthetic-cloud-v1"
NOW = 1793000000.0


def blocked(*_, **__):
    raise AssertionError("REAL_EXTERNAL_IO_FORBIDDEN")


class NodeTransport:
    """Exercise state-core.mjs using its local SQLite harness, with no HTTP socket."""
    def __init__(self, code_hash, *, oauth=False):
        node = shutil.which("node", path="/opt/homebrew/bin:/usr/bin:/bin")
        if not node:
            raise AssertionError("INSTALLED_NODE_REQUIRED")
        self.process = subprocess.Popen([node, str(Path(__file__).with_name("state-core.test.mjs")), "--protocol"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={"PATH": "/usr/bin:/bin", "NODE_NO_WARNINGS": "1"})
        self.calls = []
        self.exchange({"setup": {"deployment": DEPLOYMENT, "code_sha256": code_hash,
                                  "policy_sha256": POLICY_HASH, "token": TOKEN, "oauth": oauth}})

    def exchange(self, value):
        self.process.stdin.write(json.dumps(value, separators=(",", ":")) + "\n")
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 5)[0]:
            raise AssertionError("OFFLINE_NODE_PROTOCOL_TIMEOUT")
        line = self.process.stdout.readline()
        result = json.loads(line)
        if result.get("bridge_error"):
            raise AssertionError("OFFLINE_NODE_PROTOCOL_ERROR")
        return result

    def request(self, url, *, method="POST", headers=None, body=b"{}", limit=16384, **kwargs):
        if method != "POST":
            raise AssertionError("STATE_POST_ONLY")
        self.calls.append({"url": url, "headers": headers, "body": body})
        result = self.exchange({"url": url, "headers": headers, "body": base64.b64encode(body).decode()})
        raw = base64.b64decode(result["body"])
        if len(raw) > limit:
            raise ValueError("SYNTHETIC_LIMIT")
        return Response(result["status"], raw, url, {})

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()


class ProbeWorker:
    calls = []
    def __init__(self, store, policy, policy_hash, config, **kwargs):
        self.store = store
    def cycle(self, **kwargs):
        run_id = self.store.start_run(NOW)
        self.store.set_meta("test_runs", int(self.store.meta("test_runs", "0")) + 1)
        report = {"providers": {}, "errors": [], "new_subscriptions": 0}
        self.store.finish_run(run_id, NOW, report)
        self.calls.append(run_id)
        return report


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS /var is a symlink; fixtures must use its real path, not weaken production guards.
        self.root = Path(self.temp.name).resolve()
        self.transports = []
        self.env_reads = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(socket, "getaddrinfo", side_effect=blocked))
        self.stack.enter_context(patch.object(socket, "create_connection", side_effect=blocked))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=blocked))
        self.stack.enter_context(patch.object(urllib.request, "urlopen", side_effect=blocked))
        original = os.environ.get
        def env(name, *args):
            if any(s in name.upper() for s in ("TOKEN", "SECRET", "PASSWORD", "INOREADER", "SUPABASE", "DISCOVERY")):
                self.env_reads.append(name)
                raise AssertionError("REAL_CREDENTIAL_LOOKUP_FORBIDDEN")
            return original(name, *args)
        self.stack.enter_context(patch.object(os.environ, "get", side_effect=env))
        self.addCleanup(lambda: self.assertEqual(self.env_reads, []))
        ProbeWorker.calls = []

    def transport(self):
        value = NodeTransport(run.code_hash())
        self.transports.append(value)
        self.addCleanup(value.close)
        return value

    def config(self, name="runtime"):
        root = self.root / name
        root.mkdir(mode=0o700)
        return {"version": 1, "enabled": True, "state_endpoint": ENDPOINT,
            "code_sha256": run.code_hash(), "policy_sha256": POLICY_HASH, "policy_path": str(POLICY_PATH),
            "runtime": {"version": 1, "deployment_id": DEPLOYMENT, "runtime_root": str(root),
                "state_path": str(root / "sandbox/service"), "live_inputs": False,
                "writer_enabled": False, "collector_ready": False,
                "pilot_proofs": {"single": False, "batch3": False}, "manager": None,
                "providers": [], "artifacts": [], "delivery": {"enabled": False}}}

    def client(self, transport, **kwargs):
        return StateClient(ENDPOINT, DEPLOYMENT, run.code_hash(), POLICY_HASH,
                           lambda: TOKEN, enabled=True, transport=transport, **kwargs)

    def empty_snapshot(self):
        parent = self.root / ("seed-" + uuid.uuid4().hex)
        parent.mkdir()
        root = parent / "service"
        with Store(root, DEPLOYMENT, bootstrap=True) as store:
            return store.db.serialize()

    def boot(self, transport, raw=None):
        self.client(transport).bootstrap(self.empty_snapshot() if raw is None else raw)

    def once(self, document, transport, **kwargs):
        return run.execute(document, allow_live=True, transport=transport,
                           token_provider=lambda: TOKEN, **kwargs)

    def test_validate_no_credentials_http_or_state_creation(self):
        document = self.config()
        result = run.execute(document, mode="validate", transport=object(), token_provider=blocked)
        self.assertEqual(result["status"], "VALIDATED_NO_IO")
        self.assertFalse(Path(document["runtime"]["state_path"]).exists())
        self.assertEqual(self.env_reads, [])

    def test_allow_live_and_enabled_both_required_before_token(self):
        document = self.config()
        with self.assertRaises(Problem):
            run.execute(document, mode="once", token_provider=blocked)
        document["enabled"] = False
        with self.assertRaises(Problem):
            run.execute(document, mode="once", allow_live=True, token_provider=blocked)

    def test_bad_code_or_policy_pin_before_token(self):
        for key in ("code_sha256", "policy_sha256"):
            with self.subTest(key=key):
                document = self.config(key)
                document[key] = "f" * 64
                with self.assertRaises(Problem):
                    run.execute(document, allow_live=True, token_provider=blocked)

    def test_untrusted_state_host_paths_and_deployment_rejected(self):
        for i, url in enumerate(("http://localhost", ENDPOINT + "/", ENDPOINT + "?token=secret", "https://127.0.0.1")):
            document = self.config("bad-endpoint-" + str(i)); document["state_endpoint"] = url
            with self.assertRaises(Problem): run.execute(document, mode="validate")
        document = self.config("bad-root"); document["runtime"]["runtime_root"] = str(FIELD)
        with self.assertRaises(Problem): run.execute(document, mode="validate")
        document = self.config("bad-name"); document["runtime"]["deployment_id"] = "../escape"
        with self.assertRaises(Problem): run.execute(document, mode="validate")

    def test_apply_requires_real_pilot_manager_delivery_gates_before_cloud(self):
        document = self.config()
        with self.assertRaises(Problem):
            run.execute(document, allow_live=True, apply=True, approved_policy_hash=POLICY_HASH, token_provider=blocked)

    def test_bootstrap_creates_only_empty_cloud_journal_not_worker(self):
        document = self.config(); transport = self.transport()
        result = self.once(document, transport, mode="bootstrap", worker_factory=blocked)
        self.assertEqual(result["status"], "BOOTSTRAPPED_NO_SOURCE_IO")
        self.assertEqual([c["url"].split("/")[-1] for c in transport.calls], ["bootstrap"])

    def test_bootstrap_with_live_sources_is_rejected(self):
        document = self.config(); document["runtime"]["live_inputs"] = True
        with self.assertRaises(Problem):
            run.execute(document, mode="bootstrap", allow_live=True, token_provider=blocked)

    def test_two_jobs_actual_worker_reuse_journal_with_distinct_runs(self):
        transport = self.transport(); self.boot(transport)
        for name in ("job1", "job2"):
            result = self.once(self.config(name), transport)
            self.assertEqual(result["status"], "CYCLE_FINISHED")
            self.assertEqual(result["new_subscriptions"], 0)
        document = self.config("inspect"); client = self.client(transport); client.acquire()
        allowed, root = run.runtime_paths(document["runtime"])
        run.restore(client.download(), allowed, root, DEPLOYMENT)
        with Store(root, DEPLOYMENT) as store:
            rows = store.db.execute("SELECT id,receipt FROM runs").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({r[0] for r in rows}), 2)
            self.assertTrue(all(json.loads(r[1])["http"]["requests"] == 0 for r in rows))
        client.release(); self.assertEqual(self.env_reads, [])

    def test_resume_keeps_cursor_and_provenance(self):
        root = self.root / "seed"
        with Store(root, DEPLOYMENT, bootstrap=True) as store:
            store.ingest("radar", "public-query", [{"item_id": "article1", "publisher_url": "https://publisher.example/"}],
                         {"continuation": "SYNTHETIC_CURSOR"}, NOW)
            raw = store.db.serialize()
        transport = self.transport(); self.boot(transport, raw)
        self.once(self.config(), transport, worker_factory=ProbeWorker)
        client = self.client(transport); client.acquire(); allowed, target = run.runtime_paths(self.config("inspect")["runtime"])
        run.restore(client.download(), allowed, target, DEPLOYMENT)
        with Store(target, DEPLOYMENT) as store:
            self.assertEqual(store.cursor("radar")["continuation"], "SYNTHETIC_CURSOR")
            self.assertEqual(store.summary()["observations"], 1)
        client.release()

    def test_day_cap_survives_ephemeral_job_restart(self):
        root = self.root / "seed"
        with Store(root, DEPLOYMENT, bootstrap=True) as store:
            store.reserve("original", "first", "quickadd", NOW, day_limit=1, cycle_limit=1)
            raw = store.db.serialize()
        transport = self.transport(); self.boot(transport, raw); posted = []
        class CappedWorker(ProbeWorker):
            def cycle(self, **kwargs):
                report = {"providers": {}, "errors": [], "new_subscriptions": 0}
                try:
                    self.store.reserve("new", "second", "quickadd", NOW + 1, day_limit=1, cycle_limit=1)
                    posted.append("FAKE_POST")
                except Problem: report["errors"].append("QUOTA_BLOCKED")
                return report
        result = self.once(self.config(), transport, worker_factory=CappedWorker)
        self.assertEqual(result["status"], "CYCLE_PARTIAL"); self.assertEqual(posted, [])

    def test_failed_checkpoint_blocks_quickadd_folder_and_api_call(self):
        for i, kind in enumerate(("quickadd", "folder_edit", "discovery_api")):
            with self.subTest(kind=kind):
                transport = self.transport(); self.boot(transport); client = self.client(transport); client.acquire()
                document = self.config("fault-" + str(i)); allowed, root = run.runtime_paths(document["runtime"])
                run.restore(client.download(), allowed, root, DEPLOYMENT)
                original = transport.request
                def fail(url, **kwargs):
                    if url.endswith("/checkpoint"): raise OSError("SYNTHETIC_PRIVATE_ERROR")
                    return original(url, **kwargs)
                transport.request = fail; posted = []
                with Store(root, DEPLOYMENT, checkpoint=client.checkpoint) as store:
                    with self.assertRaises(CheckpointError):
                        store.reserve("op", "run", kind, NOW, day_limit=5, cycle_limit=1)
                        posted.append("FAKE_POST")
                    self.assertTrue(store.poisoned)
                    with self.assertRaises(CheckpointError): store.set_meta("later", "must-not-run")
                self.assertEqual(posted, [])
                with self.assertRaises(CloudHold): self.client(transport).acquire()

    def test_accepted_checkpoint_lost_ack_holds_next_job_without_retry(self):
        transport = self.transport(); self.boot(transport); client = self.client(transport); client.acquire()
        document = self.config(); allowed, root = run.runtime_paths(document["runtime"])
        run.restore(client.download(), allowed, root, DEPLOYMENT)
        original = transport.request; sends = []
        def lost(url, **kwargs):
            response = original(url, **kwargs)
            if url.endswith("/checkpoint"):
                sends.append(url); raise TimeoutError("SYNTHETIC_LOST_ACK")
            return response
        transport.request = lost
        with Store(root, DEPLOYMENT, checkpoint=client.checkpoint) as store:
            with self.assertRaises(CheckpointError):
                store.reserve("op", "run", "quickadd", NOW, day_limit=5, cycle_limit=1)
        self.assertEqual(len(sends), 1)
        with self.assertRaises(CloudHold): client.release()
        with self.assertRaises(CloudHold): self.client(transport).acquire()

    def test_killed_job_leaves_exclusive_lease(self):
        transport = self.transport(); self.boot(transport)
        class Interrupted(ProbeWorker):
            def cycle(self, **kwargs):
                self.store.set_meta("before_crash", "durable")
                raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.once(self.config(), transport, worker_factory=Interrupted)
        with self.assertRaises(CloudHold): self.client(transport).acquire()

    def test_bad_snapshot_hash_holds_before_worker(self):
        transport = self.transport(); self.boot(transport); original = transport.request
        def corrupt(url, **kwargs):
            response = original(url, **kwargs)
            return Response(200, response.body[:-1], url, {}) if url.endswith("/download") else response
        transport.request = corrupt
        with self.assertRaises(CloudHold): self.once(self.config(), transport, worker_factory=blocked)

    def test_valid_hash_of_invalid_sqlite_still_holds_before_worker(self):
        transport = self.transport(); self.boot(transport, b"SQLite format 3\x00" + b"invalid" * 100)
        with self.assertRaises(Problem): self.once(self.config(), transport, worker_factory=blocked)
        with self.assertRaises(CloudHold): self.client(transport).acquire()

    def test_missing_cloud_state_does_not_start_worker_or_bootstrap(self):
        transport = self.transport()
        with self.assertRaises(CloudHold): self.once(self.config(), transport, worker_factory=blocked)
        self.assertTrue(all(not c["url"].endswith("bootstrap") for c in transport.calls))

    def test_local_existing_directory_is_not_overwritten(self):
        document = self.config(); target = Path(document["runtime"]["state_path"])
        target.mkdir(parents=True); (target / "sentinel").write_text("preserve")
        with self.assertRaises(Problem): run.execute(document, allow_live=True, token_provider=blocked)
        self.assertEqual((target / "sentinel").read_text(), "preserve")

    def test_wrong_ack_poison_and_no_release(self):
        transport = self.transport(); self.boot(transport); client = self.client(transport); client.acquire()
        original = transport.request
        def wrong(url, **kwargs):
            response = original(url, **kwargs)
            if url.endswith("/checkpoint"):
                data = json.loads(response.body); data["revision"] += 1
                return Response(200, json.dumps(data).encode(), url, {})
            return response
        transport.request = wrong
        root = self.root / "seed2"
        with Store(root, DEPLOYMENT, bootstrap=True, checkpoint=client.checkpoint) as store:
            with self.assertRaises(CheckpointError): store.set_meta("key", "value")
        self.assertTrue(client.poisoned)
        with self.assertRaises(CloudHold): client.release()

    def test_state_call_byte_and_deadline_caps_before_send(self):
        for kind in ("calls", "bytes", "deadline"):
            transport = self.transport(); client = self.client(transport)
            if kind == "calls": client.calls = MAX_CALLS
            elif kind == "bytes": client.transferred = MAX_TRANSFER
            else: client.deadline = 0
            with self.assertRaises(CloudHold): client.acquire()
            self.assertEqual(transport.calls, [])

    def test_redirect_and_cross_origin_response_are_never_followed(self):
        class Redirect:
            calls = 0
            def request(self, url, **kwargs):
                self.calls += 1; return Response(302, b"", url, {"location": "https://attacker.example"})
        transport = Redirect()
        with self.assertRaises(CloudHold): self.client(transport).acquire()
        self.assertEqual(transport.calls, 1)

    def test_store_callback_false_is_not_a_durable_ack(self):
        root = self.root / "seed"
        with Store(root, DEPLOYMENT, bootstrap=True, checkpoint=lambda _: False) as store:
            with self.assertRaises(CheckpointError): store.set_meta("key", "value")
            self.assertTrue(store.poisoned)

    def test_local_transaction_failure_rolls_back_without_checkpoint(self):
        called = []
        with Store(self.root / "seed", DEPLOYMENT, bootstrap=True, checkpoint=lambda s: called.append(1)) as store:
            with self.assertRaises(Problem):
                with store.transaction():
                    store.db.execute("INSERT INTO metadata VALUES ('test', 'not-committed')")
                    raise Problem("SYNTHETIC_FAIL")
            self.assertIsNone(store.meta("test")); self.assertEqual(called, []); self.assertFalse(store.poisoned)

    def test_cli_validation_and_unexpected_errors_do_not_leak(self):
        document = self.config(); path = self.root / "private.json"; path.write_text(json.dumps(document))
        output = io.StringIO()
        with contextlib.redirect_stdout(output): self.assertEqual(run.main(["validate", "--config", str(path)]), 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "VALIDATED_NO_IO")
        output = io.StringIO()
        with patch.object(run, "execute", side_effect=RuntimeError(TOKEN + " https://private-alert.example/?secret=SYNTHETIC")), contextlib.redirect_stdout(output):
            self.assertEqual(run.main(["validate", "--config", str(path)]), 2)
        self.assertNotIn(TOKEN, output.getvalue()); self.assertNotIn("private-alert", output.getvalue())
        self.assertEqual(self.env_reads, [])

    def test_public_summary_contains_counts_not_private_provider_or_error(self):
        value = run.summary({"providers": {"PRIVATE_PROVIDER": {"error": TOKEN}},
                             "errors": ["https://private-alert.example/?secret=" + TOKEN], "new_subscriptions": 0}, False)
        text = json.dumps(value)
        self.assertNotIn(TOKEN, text); self.assertNotIn("PRIVATE_PROVIDER", text)
        self.assertEqual(value["error_count"], 2)


if __name__ == "__main__":
    unittest.main()
