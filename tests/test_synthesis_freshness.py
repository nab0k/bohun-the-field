"""C3-02 offline regression tests against the documented public-feed shape."""
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


def real_shape(items):
    return {"schema": "synthesis-feed", "notice": "feed notice",
            "generated_at": "2026-11-04T20:00:00Z", "items": items}


class FreshnessTests(unittest.TestCase):
    def test_real_feed_shape_and_date_only_stale(self):
        doc = real_shape([{"date": "2026-11-02", "title": "Representative item"}])
        r = probe.evaluate(doc, NOW)
        self.assertEqual(r["status"], "stale")
        self.assertEqual(r["latest_item_date"], "2026-11-02")
        self.assertEqual(r["age_days"], 2)

    def test_today_and_yesterday_are_fresh_by_calendar_days(self):
        for item_date, expected_age in [("2026-11-04", 0), ("2026-11-03", 1)]:
            with self.subTest(item_date=item_date):
                r = probe.evaluate(real_shape([{"date": item_date}]), NOW)
                self.assertEqual(r["status"], "fresh")
                self.assertEqual(r["age_days"], expected_age)

    def test_threshold_is_configurable(self):
        data = real_shape([{"date": "2026-11-02"}])
        self.assertEqual(probe.evaluate(data, NOW, max_age_days=2)["status"], "fresh")
        self.assertEqual(probe.evaluate(data, NOW, max_age_days=0)["status"], "stale")

    def test_calendar_boundary_not_24_hour_window(self):
        near_midnight = datetime(2026, 11, 4, 0, 1, tzinfo=timezone.utc)
        self.assertEqual(probe.evaluate(real_shape([{"date": "2026-11-03"}]),
                                              near_midnight)["status"], "fresh")

    def test_empty_feed_is_stale(self):
        self.assertEqual(probe.evaluate(real_shape([]), NOW)["reason"], "feed_empty")

    def test_generated_at_not_used(self):
        with self.assertRaisesRegex(ValueError, "date"):
            probe.evaluate(real_shape([{"title": "No item date"}]), NOW)

    def test_naive_datetime_is_error_even_when_item_date_only_works(self):
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            probe.evaluate(real_shape([{"date": "2026-11-04T19:00:00"}]), NOW)

    def test_offset_timestamp_converted_to_utc_date(self):
        r = probe.evaluate(real_shape([{"date": "2026-11-05T00:30:00+02:00"}]), NOW)
        self.assertEqual(r["latest_item_date"], "2026-11-04")

    def test_future_date_fails(self):
        with self.assertRaisesRegex(ValueError, "future"):
            probe.evaluate(real_shape([{"date": "2026-11-05"}]), NOW)

    def test_unknown_schema_is_error(self):
        for obj in ([], {"entries": []}, {"data": {"items": []}},
                    {"items": ["text"]}, {}):
            with self.subTest(obj=obj), self.assertRaises(ValueError):
                probe.evaluate(obj, NOW)

    def test_bad_date(self):
        with self.assertRaises(ValueError):
            probe.evaluate(real_shape([{"date": "2026-02-30"}]), NOW)

    def test_newest_item_date(self):
        r = probe.evaluate(real_shape([{"date": "2026-10-31"},
                                      {"date": "2026-11-04"}]), NOW)
        self.assertEqual(r["age_days"], 0)

    def test_offline_http_failure_sets_error(self):
        with patch.object(probe, "fetch_json", side_effect=probe.URLError("no network")):
            self.assertEqual(probe.main(["--now", "2026-11-04T20:00:00Z"]), 2)

    def test_old_news_date_with_recent_growth_is_fresh(self):
        # Real case of 04.11: new items carried a date two days old.
        grown = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)
        with patch.object(probe, "fetch_json", return_value=real_shape([{"date": "2026-11-02"}])), \
                patch.object(probe, "fetch_growth", return_value=grown):
            self.assertEqual(probe.main(["--now", "2026-11-04T20:00:00Z"]), 0)

    def test_today_news_date_without_growth_is_stale(self):
        grown = datetime(2026, 11, 3, 1, 0, tzinfo=timezone.utc)
        with patch.object(probe, "fetch_json", return_value=real_shape([{"date": "2026-11-04"}])), \
                patch.object(probe, "fetch_growth", return_value=grown):
            self.assertEqual(probe.main(["--now", "2026-11-04T20:00:00Z"]), 1)

    def test_history_failure_is_error_not_fresh(self):
        with patch.object(probe, "fetch_json", return_value=real_shape([{"date": "2026-11-04"}])), \
                patch.object(probe, "fetch_growth", side_effect=probe.URLError("rate limited")):
            self.assertEqual(probe.main(["--now", "2026-11-04T20:00:00Z"]), 2)

    def test_limit_boundary(self):
        base = probe.evaluate(real_shape([{"date": "2026-11-04"}]), NOW)
        at = datetime(2026, 11, 3, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(probe.apply_growth(base, at, NOW, 30)["status"], "fresh")
        self.assertEqual(probe.apply_growth(base, at, NOW, 29.9)["status"], "stale")
        self.assertEqual(probe.apply_growth(base, None, NOW, 30)["reason"], "no_growth_in_checked_history")

    def test_empty_feed_needs_no_history(self):
        with patch.object(probe, "fetch_json", return_value=real_shape([])), \
                patch.object(probe, "fetch_growth", side_effect=AssertionError("must not be called")):
            self.assertEqual(probe.main(["--now", "2026-11-04T20:00:00Z"]), 1)

    def test_last_growth_skips_commits_without_new_ids(self):
        def commit(sha, stamp):
            return {"sha": sha, "commit": {"committer": {"date": stamp}}}
        commits = [commit("c", "2026-11-04T18:00:00Z"), commit("b", "2026-11-04T16:00:00Z"), commit("a", "2026-11-03T16:00:00Z")]
        ids = {"c": {1, 2}, "b": {1, 2}, "a": {1}}
        self.assertEqual(probe.last_growth(commits, ids.__getitem__).hour, 16)
        self.assertEqual(probe.last_growth(commits, ids.__getitem__).day, 4)
        same = {"c": {1}, "b": {1}, "a": {1}}
        self.assertIsNone(probe.last_growth(commits, same.__getitem__))
        with self.assertRaises(ValueError):
            probe.last_growth(commits[:1], ids.__getitem__)


if __name__ == "__main__":
    unittest.main()
