"""CLI import/default/live authorization and immutable output tests, all fake I/O."""
from contextlib import redirect_stdout
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
import ssl
import subprocess
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

with (patch("socket.socket", side_effect=AssertionError("IMPORT_NETWORK")),
      patch("socket.getaddrinfo", side_effect=AssertionError("IMPORT_DNS")),
      patch("urllib.request.urlopen", side_effect=AssertionError("IMPORT_HTTP")),
      patch("sqlite3.connect", side_effect=AssertionError("IMPORT_DB")),
      patch("subprocess.Popen", side_effect=AssertionError("IMPORT_PROCESS"))):
    from collectors.inoreader import batch_cli as cli
    from collectors.inoreader import batch_delivery as delivery

from test_inoreader_batch_delivery import make_plan, snapshot, article


class FakeClient:
    def __init__(self, provider, budget, plan, fail=False):
        self.provider, self.budget, self.plan, self.fail = provider, budget, plan, fail
        self.calls = []
    def subscriptions(self):
        self.calls.append("subscriptions")
        self.budget.reserve()
        self.provider()
        if self.fail:
            raise RuntimeError("SECRET_SENTINEL")
        return snapshot(self.plan)
    def stream(self, fid):
        self.calls.append(fid)
        self.budget.reserve()
        return {"items": [article(fid)]}


class CLITests(unittest.TestCase):
    def setUp(self):
        for name in ("socket.socket", "socket.getaddrinfo", "urllib.request.urlopen", "sqlite3.connect", "subprocess.Popen"):
            guard = patch(name, side_effect=AssertionError("UNEXPECTED_EXTERNAL_IO"))
            guard.start()
            self.addCleanup(guard.stop)
        self.temp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.plan = make_plan(2)
        self.planpath = self.root/"plan.json"
        self.planpath.write_text(delivery.json_text(self.plan), encoding="utf-8")

    def invoke(self, mode=None, flags=(), **kwargs):
        args = ([] if mode is None else [mode]) + ["--plan", str(self.planpath),
                "--output", str(self.root/"out"), *flags]
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = cli.main(args, output_root=self.root, **kwargs)
        return code, stream.getvalue()

    def approval(self):
        return dict(schema="bohun-inoreader-capture-approval-v1", authorized=True,
                    authorization_ref="synthetic-run", plan_sha256=delivery.digest(self.planpath.read_text()),
                    code_pins=cli.code_pins(), caps=self.plan["caps"], producer_sha="a"*40,
                    pipeline_version="preview-v1", token_env="INOREADER_ACCESS_TOKEN",
                    subscription_contract_verified=True, provider_content_retention_approved=True)

    def test_default_validate_no_provider_env_network_or_db_and_truthful_status(self):
        with patch.object(cli.os.environ, "get", side_effect=AssertionError("ENV_READ")):
            code, text = self.invoke(client_factory=lambda *_: self.fail("NO_CLIENT"))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["status"], "VALIDATED_PLAN_ONLY_NO_CAPTURE")
        saved = json.loads((self.root/"out/preview.json").read_text())
        self.assertNotIn("batches", saved)
        self.assertEqual(saved["profile_count"], 2)

    def test_reloading_production_modules_has_no_io_or_env_reads(self):
        with patch.object(cli.os.environ, "get", side_effect=AssertionError("ENV_READ")):
            for module in (delivery, cli):
                spec = importlib.util.spec_from_file_location(
                    "collectors.inoreader._isolated_"+module.__name__.rsplit(".", 1)[-1], module.__file__)
                fresh = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(fresh)

    def test_live_gates_precede_credential_and_provider_access(self):
        for flags in ((), ("--allow-live",)):
            code, text = self.invoke("capture", flags=flags,
                                     token_provider=lambda: self.fail("TOKEN_READ"),
                                     client_factory=lambda *_: self.fail("CLIENT_CREATED"))
            self.assertEqual(code, 2)
            self.assertIn("LIVE_APPROVAL_REQUIRED", text)
        self.assertFalse((self.root/"out").exists())

    def test_plan_code_retention_contract_and_producer_pins_reject_before_marker(self):
        for change in (dict(plan_sha256="0"*64), dict(code_pins={}), dict(provider_content_retention_approved=False),
                       dict(subscription_contract_verified=False), dict(producer_sha="bad"), dict(token_env="OTHER_SECRET")):
            approval = self.approval() | change
            path = self.root/"approval.json"
            path.write_text(delivery.json_text(approval))
            code, text = self.invoke("capture", flags=("--allow-live", "--approval-manifest", str(path)),
                                     token_provider=lambda: self.fail("TOKEN_READ"),
                                     client_factory=lambda *_: self.fail("CLIENT_CREATED"))
            self.assertEqual(code, 2)
        self.assertFalse((self.root/"authorizations").exists())

    def test_fake_authorized_capture_once_and_marker_prevents_second_provider_call(self):
        path = self.root/"approval.json"
        path.write_text(delivery.json_text(self.approval()))
        clients = []
        def factory(provider, budget):
            c = FakeClient(provider, budget, self.plan)
            clients.append(c)
            return c
        flags = ("--allow-live", "--approval-manifest", str(path))
        code, text = self.invoke("capture", flags=flags, client_factory=factory,
                                 token_provider=lambda: "SYNTHETIC_TOKEN")
        self.assertEqual(code, 0)
        self.assertEqual(len(clients[0].calls), 3)
        self.assertEqual(json.loads(text)["prepared"], 2)
        self.assertTrue((self.root/"authorizations/synthetic-run.json").exists())
        # A different output cannot reuse the consumed approval.
        (self.root/"out").rename(self.root/"saved")
        code, _ = self.invoke("capture", flags=flags, client_factory=factory,
                              token_provider=lambda: self.fail("TOKEN_REPLAY"))
        self.assertEqual(code, 2)
        self.assertEqual(len(clients), 1)

    def test_failed_provider_retains_marker_and_fixed_failure_bundle(self):
        path = self.root/"approval.json"
        path.write_text(delivery.json_text(self.approval()))
        code, text = self.invoke("capture", flags=("--allow-live", "--approval-manifest", str(path)),
                                 token_provider=lambda: "SYNTHETIC_TOKEN",
                                 client_factory=lambda p, b: FakeClient(p, b, self.plan, fail=True))
        self.assertEqual(code, 2)
        self.assertNotIn("SECRET_SENTINEL", text)
        saved = (self.root/"out/preview.json").read_text()
        self.assertNotIn("SECRET_SENTINEL", saved)
        self.assertEqual(json.loads(saved)["status"], "CAPTURE_FAILED")
        self.assertEqual(len(json.loads(saved)["outcomes"]), 2)
        self.assertTrue((self.root/"authorizations/synthetic-run.json").exists())
