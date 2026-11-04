"""Offline unit tests for C3-02. Run: python -m unittest discover -s tests -p 'test_synthesis_freshness.py'."""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_synthesis_freshness.py"
spec = importlib.util.spec_from_file_location("check_synthesis_freshness", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

NOW = datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)


class FreshnessTests(unittest.TestCase):
    def test_fresh_publication(self):
        r = probe.evaluate({"items": [{"published_at": "2026-11-04T19:00:00Z"}]}, NOW)
        self.assertEqual(r["status"], "fresh")
        self.assertEqual(r["age_hours"], 1.0)

    def test_older_than_24_hours_warns(self):
        r = probe.evaluate({"syntheses": [{"published_at": "2026-11-03T19:59:59Z"}]}, NOW)
        self.assertEqual(r["status"], "stale")
        self.assertIn("WARNING", probe.markdown(r))

    def test_exact_24_hours_passes(self):
        r = probe.evaluate([{"published_at": "2026-11-03T20:00:00+00:00"}], NOW)
        self.assertEqual(r["status"], "fresh")

    def test_empty_feed_is_stale_not_success(self):
        self.assertEqual(probe.evaluate({"items": []}, NOW)["reason"], "feed_empty")

    def test_feed_generated_at_not_mistaken_for_publication(self):
        with self.assertRaisesRegex(ValueError, "timestamp"):
            probe.evaluate({"generated_at": "2026-11-04T20:00:00Z",
                            "items": [{"title": "Old material"}]}, NOW)

    def test_bad_dates_fail_closed(self):
        for value in ("not-a-date", "2026-11-04T19:00:00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.evaluate([{"published_at": value}], NOW)

    def test_future_date_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "future"):
            probe.evaluate([{"published_at": "2026-11-05T00:00:00Z"}], NOW)

    def test_multi_page_items_use_newest(self):
        document = {"data": {"items": [
            {"date": "2026-11-01T10:00:00+00:00"},
            {"date": "2026-11-04T18:00:00+00:00"}]}}
        self.assertEqual(probe.evaluate(document, NOW)["status"], "fresh")

    def test_wrong_shape_errors(self):
        for data in ({}, {"items": {"invalid": True}}, {"items": ["text"]}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                probe.evaluate(data, NOW)

    def test_offline_http_error_sets_error_not_stale(self):
        with patch.object(probe, "fetch_json", side_effect=probe.URLError("network down")):
            self.assertEqual(probe.main(["--now", "2026-11-04T20:00:00Z"]), 2)

    def test_offline_stale_exit_code(self):
        with patch.object(probe, "fetch_json", return_value={"items": [{"date": "2026-11-01T00:00:00Z"}]}):
            self.assertEqual(probe.main(["--now", "2026-11-04T20:00:00Z"]), 1)

    def test_offline_fresh_exit_code(self):
        with patch.object(probe, "fetch_json", return_value={"items": [{"date": "2026-11-04T19:00:00Z"}]}):
            self.assertEqual(probe.main(["--now", "2026-11-04T20:00:00Z"]), 0)


if __name__ == "__main__":
    unittest.main()
