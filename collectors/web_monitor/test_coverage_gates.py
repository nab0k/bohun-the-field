"""C1-06: offline fail-closed coverage gates for the Web Monitor CLI.

All subprocess/network interfaces are replaced with synthetic in-memory results.
"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from collectors.web_monitor import monitor


class CoverageGatesTests(unittest.TestCase):
    def run_batch(self, sources, *, warnings=None, candidates=1,
                  channel_configured=True):
        warnings = [] if warnings is None else warnings
        stdout = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sources.json"
            channels = Path(tmp) / "channels.json"
            src.write_text(json.dumps(sources), encoding="utf-8")
            channels.write_text("[]", encoding="utf-8")
            worker = {
                "name": "Test", "candidates": candidates, "inserted": 0,
                "quality": {"possible_article": candidates, "review": 0, "index": 0},
                "warnings": warnings, "channel_configured": channel_configured
            }
            command = ["monitor.py", "--sources", str(src),
                       "--channels", str(channels)]
            with patch.object(sys, "argv", command), patch.object(
                monitor.subprocess, "run",
                return_value=subprocess.CompletedProcess(
                    command, 0, stdout=json.dumps(worker) + "\n", stderr=""
                )
            ) as fake_worker, contextlib.redirect_stdout(stdout):
                try:
                    monitor.main()
                    code = 0
                except SystemExit as exc:
                    code = exc.code
            return code, stdout.getvalue(), fake_worker.call_count

    @staticmethod
    def source(enabled=True):
        return {"name": "Test", "country": "US",
                "url": "https://example.org/", "enabled": enabled}

    def test_empty_sources_are_not_healthy(self):
        code, output, workers = self.run_batch([])
        self.assertEqual(code, 2)
        self.assertEqual(workers, 0)
        self.assertIn('"no_enabled_sources": 1', output)
        self.assertIn("sources_checked=0", output)

    def test_only_disabled_sources_are_not_healthy(self):
        code, output, workers = self.run_batch([self.source(False)])
        self.assertEqual(code, 2)
        self.assertEqual(workers, 0)
        self.assertIn("no_enabled_sources", output)

    def test_valid_source_with_candidates_and_no_warnings_is_healthy(self):
        code, output, workers = self.run_batch([self.source()])
        self.assertEqual((code, workers), (0, 1))
        self.assertIn("sources_checked=1 candidates=1", output)
        self.assertIn("COVERAGE_GAPS {}", output)

    def test_warning_with_candidates_fails_coverage(self):
        code, output, workers = self.run_batch(
            [self.source()], warnings=["channel_timeout"], candidates=3)
        self.assertEqual((code, workers), (2, 1))
        self.assertIn("sources_checked=1 candidates=3", output)
        self.assertIn("SOURCE_COVERAGE Test: source_error", output)
        self.assertIn('"source_error": 1', output)

    def test_missing_enabled_channel_is_gap_even_with_candidates(self):
        code, output, workers = self.run_batch(
            [self.source()], candidates=1, channel_configured=False)
        self.assertEqual((code, workers), (2, 1))
        self.assertIn('"no_enabled_channel": 1', output)

    def test_no_candidate_with_no_warnings_is_gap(self):
        code, output, workers = self.run_batch([self.source()], candidates=0)
        self.assertEqual((code, workers), (2, 1))
        self.assertIn('"no_candidates": 1', output)

    def test_warning_with_no_candidates_counts_only_one_error(self):
        code, output, workers = self.run_batch(
            [self.source()], warnings=["robots_disallowed"], candidates=0)
        self.assertEqual((code, workers), (2, 1))
        self.assertIn('"source_error": 1', output)
        self.assertIn("sources_without_candidates_or_failed=1", output)
        self.assertNotIn('"no_candidates": 1', output)


if __name__ == "__main__":
    unittest.main()
