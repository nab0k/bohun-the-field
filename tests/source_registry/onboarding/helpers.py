"""Synthetic test inputs and external-I/O tripwires, installed BEFORE package imports."""
import contextlib
import copy
import http.client
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
sys.path.insert(0, str(ROOT))
GUARDS = contextlib.ExitStack()
BLOCKED_ATTEMPTS = []


def blocked(*args, **kwargs):
    BLOCKED_ATTEMPTS.append("intentional_or_unexpected_external_boundary")
    raise AssertionError("EXTERNAL_IO_FORBIDDEN")


for owner, names in [(socket, ("socket", "create_connection", "getaddrinfo", "gethostbyname")),
                     (os, tuple(n for n in ("system", "popen", "posix_spawn", "posix_spawnp") if hasattr(os, n))),
                     (urllib.request, ("urlopen", "build_opener")),
                     (http.client.HTTPConnection, ("connect",)),
                     (http.client.HTTPSConnection, ("connect",)),
                     (sqlite3, ("connect",)),
                     (subprocess, ("Popen", "run", "call", "check_call", "check_output"))]:
    for name in names:
        GUARDS.enter_context(mock.patch.object(owner, name, blocked))

# These imports MUST remain after tripwire setup.
from collectors.source_registry.onboarding import adapters, artifact, audit, contracts, registry

WHEN = "2026-10-24T00:00:00Z"
PRODUCER = {"version": "1.0.0", "git_sha": "a" * 40, "code_hash": "b" * 64,
            "tree_state": "DIRTY_ISOLATED"}


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def master():
    rows = fixture("registry_sources_synthetic.json")["rows"]
    lines = ["# SYNTHETIC planning fixture", "| " + " | ".join(registry.HEADERS) + " |",
             "| " + " | ".join(["---"] * 6) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return ("\n".join(lines) + "\n").encode()


def inputs():
    return {"grants": (FIXTURES / "grants_synthetic.json").read_bytes(),
            "openalex": (FIXTURES / "openalex_synthetic.json").read_bytes()}


def bundle():
    data = master()
    return audit.prepare_bundle(data, contracts.sha256(data), inputs(), PRODUCER, WHEN)


def mapped(adapter="grants", report=None):
    if report is None:
        report = fixture(adapter + "_synthetic.json")
    return adapters.adapt(copy.deepcopy(report), adapter, adapters.profile(adapter, "c" * 64), WHEN)


@contextlib.contextmanager
def temporary():
    # /tmp and /var are symlinks on macOS. Use the canonical, explicitly private root.
    with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
        yield Path(directory).resolve()


def load_cli():
    spec = importlib.util.spec_from_file_location("onboarding_cli", ROOT / "scripts/source-connection-audit.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
