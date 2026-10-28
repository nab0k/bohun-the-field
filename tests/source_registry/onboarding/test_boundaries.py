import ast
import contextlib
import importlib
import io
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import unittest
from unittest import mock
import urllib.request

from helpers import (ROOT, WHEN, PRODUCER, audit, bundle, contracts as c, inputs, load_cli,
                     master, temporary)


class BoundaryTests(unittest.TestCase):
    def test_tripwires_block_network_db_and_subprocess(self):
        for action in (lambda: socket.create_connection(("invalid.example", 80)),
                       lambda: socket.getaddrinfo("invalid.example", 80),
                       lambda: urllib.request.urlopen("https://invalid.example"),
                       lambda: sqlite3.connect(":memory:"),
                       lambda: subprocess.run(["echo", "forbidden"])):
            with self.assertRaisesRegex(AssertionError, "EXTERNAL_IO_FORBIDDEN"): action()

    def test_imports_have_no_file_writes_or_directory_creation(self):
        with mock.patch.object(Path, "write_text", side_effect=AssertionError("IMPORT_WRITE")), \
             mock.patch.object(Path, "write_bytes", side_effect=AssertionError("IMPORT_WRITE")), \
             mock.patch.object(Path, "mkdir", side_effect=AssertionError("IMPORT_MKDIR")), \
             mock.patch.object(Path, "resolve", side_effect=AssertionError("IMPORT_RESOLVE")), \
             mock.patch("builtins.open", side_effect=AssertionError("IMPORT_OPEN")), \
             mock.patch.object(os, "open", side_effect=AssertionError("IMPORT_OS_OPEN")):
            for name in ("contracts", "registry", "adapters", "artifact", "audit"):
                importlib.reload(importlib.import_module("collectors.source_registry.onboarding." + name))
            load_cli()

    def test_modules_do_not_import_existing_collectors_or_clients(self):
        for p in (ROOT / "collectors/source_registry/onboarding").glob("*.py"):
            tree = ast.parse(p.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertTrue(all(n.name.split(".")[0] in
                                         {"argparse", "datetime", "hashlib", "json", "os", "re", "stat", "tempfile"}
                                         for n in node.names), p.name)
                if isinstance(node, ast.ImportFrom):
                    self.assertTrue(node.level == 1 or node.module == "pathlib", (p.name, node.module))
                if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os":
                    self.assertNotIn(node.attr, {"system", "popen", "posix_spawn", "posix_spawnp", "getenv", "environ"})

    def test_exclusive_output_success_readback_and_no_overwrite(self):
        with temporary() as root:
            output = root / "run"; b = bundle()
            result = audit.write_bundle(output, b, private_root=root)
            self.assertEqual(result, c.sha256(b["run_manifest.json"]))
            self.assertEqual(audit.read_bundle(output)["files"], audit.validate_bundle(b)["files"])
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            with self.assertRaisesRegex(c.ContractError, "OUTPUT_EXISTS"):
                audit.write_bundle(output, b, private_root=root)
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})

    def test_incomplete_crash_is_not_a_committed_bundle_and_old_state_survives(self):
        with temporary() as root:
            old = root / "old-state"; old.write_bytes(b"unchanged")
            output = root / "broken"; real_link = os.link; calls = []
            def fail_second(source, target, **kwargs):
                calls.append(target)
                if len(calls) == 2: raise OSError("synthetic disk failure")
                return real_link(source, target, **kwargs)
            with mock.patch.object(os, "link", side_effect=fail_second):
                with self.assertRaisesRegex(c.ContractError, "OUTPUT_IO_INCOMPLETE"):
                    audit.write_bundle(output, bundle(), private_root=root)
            self.assertFalse((output / "run_manifest.json").exists())
            self.assertEqual(old.read_bytes(), b"unchanged")
            with self.assertRaisesRegex(c.ContractError, "INCOMPLETE_BUNDLE"): audit.read_bundle(output)
            with self.assertRaises(c.ContractError): audit.write_bundle(output, bundle(), private_root=root)

    def test_invalid_bundle_is_rejected_before_output_reservation(self):
        with temporary() as root:
            b = bundle(); b["audit_report.json"] += b"x"
            with self.assertRaises(c.ContractError): audit.write_bundle(root / "run", b, private_root=root)
            self.assertFalse((root / "run").exists())

    def test_traversal_wrong_scope_symlink_and_public_paths_are_rejected(self):
        with temporary() as root:
            source = root / "input"; source.write_bytes(b"{}")
            link = root / "alias"; link.symlink_to(source)
            with self.assertRaisesRegex(c.ContractError, "SYMLINK_PATH"): c.read_bytes(link)
            with self.assertRaises(c.ContractError): c.read_bytes(root / ".." / root.name / "input")
            d = root / "real"; d.mkdir(); alias = root / "aliasdir"; alias.symlink_to(d, target_is_directory=True)
            with self.assertRaises(c.ContractError): audit.write_bundle(alias / "run", bundle(), private_root=alias)
            with self.assertRaises(c.ContractError): audit.write_bundle(root / "nested/run", bundle(), private_root=root)
            public = root / "public"; public.mkdir()
            with self.assertRaises(c.ContractError): audit.write_bundle(public / "run", bundle(), private_root=public)

    def test_large_and_non_regular_input_rejected(self):
        with temporary() as root:
            p = root / "big"; p.write_bytes(b"x" * (c.MAX_BYTES + 1))
            with self.assertRaisesRegex(c.ContractError, "INPUT_SIZE"): c.read_bytes(p)
            with self.assertRaises(c.ContractError): c.read_bytes(root)
            fifo = root / "fifo"; os.mkfifo(fifo)
            with self.assertRaisesRegex(c.ContractError, "NOT_REGULAR_FILE"): c.read_bytes(fifo)

    def test_in_process_cli_and_unknown_legacy_flags(self):
        with temporary() as root:
            data = master(); mp = root / "master.md"; mp.write_bytes(data)
            paths = {}
            for name, content in inputs().items():
                paths[name] = root / (name + ".json"); paths[name].write_bytes(content)
            args = ["--master", str(mp), "--master-sha256", c.sha256(data),
                    "--grants", str(paths["grants"]), "--openalex", str(paths["openalex"]),
                    "--producer-git-sha", PRODUCER["git_sha"], "--producer-code-hash", PRODUCER["code_hash"],
                    "--observed-at", WHEN, "--synthetic", "--output", str(root / "cli-run")]
            with mock.patch.object(audit, "PRIVATE_ROOT", root), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(load_cli().main(args), 0)
                self.assertEqual(load_cli().main(args), 2)  # No overwrite on repeated invocation.
            self.assertEqual(audit.read_bundle(root / "cli-run")["producer"], PRODUCER)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                audit.main(args + ["--source-id", "2"])

    def test_cli_master_change_fails_before_any_output(self):
        with temporary() as root:
            mp = root / "master.md"; mp.write_bytes(master())
            p = root / "input.json"; p.write_bytes(inputs()["grants"])
            args = ["--master", str(mp), "--master-sha256", "0" * 64,
                    "--grants", str(p), "--openalex", str(p), "--producer-git-sha", PRODUCER["git_sha"],
                    "--producer-code-hash", PRODUCER["code_hash"], "--observed-at", WHEN,
                    "--synthetic", "--output", str(root / "absent")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(audit.main(args, permitted_root=root), 2)
            self.assertFalse((root / "absent").exists())
