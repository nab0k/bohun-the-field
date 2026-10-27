"""Offline input-contract checks; no live OAuth, API, database or model calls."""

import contextlib
import copy
import datetime as dt
import hashlib
import io
import json
import sys
import unittest
from unittest.mock import patch

from collectors.inoreader import content_contract as content
from collectors.inoreader import sync


NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)


def capture(item, **kwargs):
    return content.extract_inoreader_content(item, captured_at=NOW, **kwargs)


def source_item(identifier="item-1", html="<p>PUBLIC_BODY_SENTINEL</p>"):
    return {"id": identifier, "title": "Headline", "published": 1792238400,
            "canonical": [{"href": "https://example.test/news"}],
            "origin": {"title": "Example publisher", "streamId": "feed/https://example.test/feed"},
            "summary": {"content": html}}


class NoNetworkTest(unittest.TestCase):
    def setUp(self):
        guard = patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected network access"))
        guard.start()
        self.addCleanup(guard.stop)


class ContentContractTests(NoNetworkTest):
    def test_preserves_body_and_separate_hashes_without_approval(self):
        html = "  <p>Factory output increased to <strong>24 systems</strong>.</p>\n"
        result = capture({"summary": {"content": html}})
        self.assertEqual(result["raw_html"], html)
        self.assertEqual(result["raw_html_sha256"], hashlib.sha256(html.encode()).hexdigest())
        self.assertEqual(result["text"], "Factory output increased to 24 systems .")
        self.assertEqual(result["text_sha256"], hashlib.sha256(result["text"].encode()).hexdigest())
        self.assertNotEqual(result["raw_html_sha256"], result["text_sha256"])
        self.assertEqual(result["status"], "BODY_PRESENT_COMPLETENESS_UNVERIFIED")
        self.assertEqual(result["completeness"], "UNKNOWN")
        for field in ("qa_status", "public_release", "evidence_eligible"):
            self.assertNotIn(field, result)

    def test_missing_body_is_metadata_only(self):
        for item in ({"title": "No body"}, {"summary": None}, {"summary": {}},
                     {"summary": {"content": " \n\t"}}):
            with self.subTest(item=item):
                result = capture(item)
                self.assertEqual(result["status"], "METADATA_ONLY")
                self.assertIsNone(result["text"])
                self.assertIsNone(result["raw_html_sha256"])

    def test_unsupported_types_are_not_coerced_to_article_text(self):
        for item in ([], {"summary": []}, {"summary": {"content": False}},
                     {"summary": {"content": {"body": "secret"}}}):
            with self.subTest(item=item):
                result = capture(item)
                self.assertEqual(result["status"], "UNSUPPORTED_SHAPE")
                self.assertIsNone(result["raw_html"])
                self.assertIsNone(result["text"])

    def test_invalid_unicode_is_retained_as_a_status_only(self):
        result = capture({"summary": {"content": "\ud800"}})
        self.assertEqual(result["issues"], ["INVALID_UTF8"])
        self.assertIsNone(result["raw_html"])
        json.dumps(result, ensure_ascii=False).encode("utf-8")

    def test_scripts_styles_and_nested_svg_are_not_article_text(self):
        html = "<style>.secret{display:none}</style><p>Evidence</p><svg><text>hidden</text></svg><script>alert(1)</script>"
        self.assertEqual(capture({"summary": {"content": html}})["text"], "Evidence")
        self.assertEqual(content.visible_text("<svg><svg>hidden</svg>also hidden</svg>shown"), "shown")
        self.assertEqual(content.visible_text("<svg/>shown"), "shown")

    def test_body_with_only_non_article_content_is_explicit(self):
        result = capture({"summary": {"content": "<script>not executed</script>"}})
        self.assertEqual(result["status"], "EMPTY_TEXT")
        self.assertEqual(result["text"], "")
        self.assertEqual(result["text_characters"], 0)
        self.assertEqual(result["raw_html"], "<script>not executed</script>")

    def test_unicode_entities_and_equivalent_utc_time(self):
        result = content.extract_inoreader_content(
            {"summary": {"content": "<p>Україна&nbsp;&amp; ЄС\n  2026</p>"}},
            captured_at=NOW.astimezone(dt.timezone(dt.timedelta(hours=2))))
        self.assertEqual(result["text"], "Україна & ЄС 2026")
        self.assertEqual(result["text_bytes"], len(result["text"].encode()))
        self.assertEqual(result["captured_at"], NOW.isoformat())

    def test_naive_time_and_invalid_limits_are_rejected(self):
        with self.assertRaises(ValueError):
            content.extract_inoreader_content({}, captured_at=dt.datetime(2026, 10, 17))
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit):
                with self.assertRaises(ValueError):
                    capture({}, max_html_bytes=limit)
                with self.assertRaises(ValueError):
                    content.CaptureBudget(limit)

    def test_html_limit_counts_utf8_bytes_not_characters(self):
        result = capture({"summary": {"content": "éé"}}, max_html_bytes=3)
        self.assertEqual(result["status"], "TOO_LARGE")
        self.assertEqual(result["raw_html_bytes"], 4)
        self.assertIsNone(result["raw_html"])
        self.assertEqual(capture({"summary": {"content": "éé"}}, max_html_bytes=4)["text"], "éé")

    def test_text_limit_does_not_publish_a_silently_truncated_prefix(self):
        result = capture({"summary": {"content": "<p>abcde</p>"}}, max_text_bytes=4)
        self.assertEqual(result["status"], "TOO_LARGE")
        self.assertEqual(result["issues"], ["TEXT_LIMIT_EXCEEDED"])
        self.assertIsNone(result["text"])
        self.assertIsNone(result["raw_html"])
        self.assertEqual(capture({"summary": {"content": "<p>abcd</p>"}}, max_text_bytes=4)["text"], "abcd")

    def test_fixed_capture_time_is_reproducible_and_input_is_not_mutated(self):
        item = source_item()
        before = copy.deepcopy(item)
        self.assertEqual(capture(item), capture(item))
        self.assertEqual(item, before)


class CaptureBudgetTests(NoNetworkTest):
    def test_budget_includes_json_escaping_and_all_status_records(self):
        budget = content.CaptureBudget()
        records = [capture(source_item(html='<p>"quotes" \\ slashes</p>'), budget=budget),
                   capture({}, budget=budget), capture({"summary": []}, budget=budget)]
        actual = sum(len(json.dumps(c, ensure_ascii=False, separators=(",", ":")).encode()) for c in records)
        self.assertEqual(budget.bytes_used, actual)
        self.assertLessEqual(actual, budget.limit)

    def test_later_metadata_can_reclaim_a_retained_body_without_losing_its_identity(self):
        first = capture(source_item(html="<p>" + "x" * 1000 + "</p>"))
        budget = content.CaptureBudget(content.capture_size(first) + 1)
        budget.add(first)
        second = capture({}, budget=budget)
        self.assertEqual(first["status"], "CAPTURE_BUDGET_EXHAUSTED")
        self.assertIsNone(first["text"])
        self.assertIsNone(first["raw_html"])
        self.assertEqual(first["captured_at"], NOW.isoformat())
        self.assertEqual(second["status"], "METADATA_ONLY")
        self.assertEqual(budget.bytes_used, sum(content.capture_size(c) for c in (first, second)))
        self.assertLessEqual(budget.bytes_used, budget.limit)

    def test_excess_body_gets_explicit_status_and_no_partial_text(self):
        item = source_item(html="<p>" + "x" * 1000 + "</p>")
        budget = content.CaptureBudget(content.capture_size(capture(item)) + 600)
        first, second = capture(item, budget=budget), capture(item, budget=budget)
        self.assertEqual(first["status"], "BODY_PRESENT_COMPLETENESS_UNVERIFIED")
        self.assertEqual(second["status"], "CAPTURE_BUDGET_EXHAUSTED")
        self.assertIsNone(second["text"])
        self.assertLessEqual(budget.bytes_used, budget.limit)

    def test_impossible_status_only_budget_fails_without_exceeding_limit(self):
        budget = content.CaptureBudget(1)
        with self.assertRaisesRegex(RuntimeError, "CAPTURE_METADATA_BUDGET_EXHAUSTED"):
            capture({}, budget=budget)
        self.assertEqual(budget.bytes_used, 0)


class NormalizerTests(NoNetworkTest):
    def test_existing_fields_are_preserved_and_body_is_only_nested(self):
        item = source_item()
        result = sync.normalize(item, "BOHUN_MEDIA", captured_at=NOW)
        self.assertEqual(set(result), {"external_id", "title", "url", "published_at", "raw_data"})
        expected = {"inoreader_id": "item-1", "folder": "BOHUN_MEDIA", "origin_title": "Example publisher",
                    "origin_stream_id": "feed/https://example.test/feed", "published": item["published"],
                    "url": "https://example.test/news", "title": "Headline"}
        self.assertEqual({k: v for k, v in result["raw_data"].items() if k != "content_capture"}, expected)
        self.assertEqual(result["raw_data"]["content_capture"]["provider_field"], "summary.content")
        self.assertEqual(result["external_id"], "item-1")

    def run_main(self, records_by_folder, *, dry_run=True, budget=None):
        subscriptions = [{"categories": [{"label": folder}]} for folder in records_by_folder]
        stdout, stored = io.StringIO(), []

        def fake_import(items):
            stored.extend(items)
            return len(items), len(items)

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(sys, "argv", ["sync.py"] + (["--dry-run"] if dry_run else [])))
            stack.enter_context(patch.object(sync, "access_token", return_value="FAKE_TOKEN_SENTINEL"))
            stack.enter_context(patch.object(sync, "api", return_value={"subscriptions": subscriptions}))
            stack.enter_context(patch.object(sync, "stream_items", side_effect=lambda folder, *_: iter(records_by_folder[folder])))
            writer = stack.enter_context(patch.object(sync, "import_items", side_effect=fake_import))
            if budget is not None:
                stack.enter_context(patch.object(sync, "CaptureBudget", return_value=budget))
            stack.enter_context(contextlib.redirect_stdout(stdout))
            sync.main()
        return stdout.getvalue(), stored, writer

    def test_dry_run_with_mock_sources_never_calls_writer_or_logs_body(self):
        output, stored, writer = self.run_main({"BOHUN_MEDIA": [source_item()]})
        writer.assert_not_called()
        self.assertEqual(stored, [])
        self.assertIn("BODY_PRESENT_COMPLETENESS_UNVERIFIED", output)
        self.assertNotIn("PUBLIC_BODY_SENTINEL", output)
        self.assertNotIn("FAKE_TOKEN_SENTINEL", output)

    def test_duplicates_across_folders_keep_first_origin_and_do_not_consume_budget_twice(self):
        item = source_item()
        budget = content.CaptureBudget()
        output, stored, writer = self.run_main({"BOHUN_A": [item], "BOHUN_B": [item]},
                                               dry_run=False, budget=budget)
        writer.assert_called_once()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["raw_data"]["folder"], "BOHUN_A")
        self.assertEqual(budget.bytes_used, content.capture_size(stored[0]["raw_data"]["content_capture"]))
        self.assertIn("VALIDATED unique_items=1", output)

    def test_global_budget_applies_to_the_batch_received_by_mock_writer(self):
        raw = source_item(html="<p>" + "x" * 1000 + "</p>")
        budget = content.CaptureBudget(content.capture_size(capture(raw)) + 600)
        _, stored, _ = self.run_main({"BOHUN_MEDIA": [raw, source_item("item-2", raw["summary"]["content"])]},
                                      dry_run=False, budget=budget)
        captures = [item["raw_data"]["content_capture"] for item in stored]
        self.assertEqual({c["status"] for c in captures},
                         {"BODY_PRESENT_COMPLETENESS_UNVERIFIED", "CAPTURE_BUDGET_EXHAUSTED"})
        self.assertLessEqual(sum(content.capture_size(c) for c in captures), budget.limit)

    def test_budget_failure_stops_before_any_database_write(self):
        with (patch.object(sys, "argv", ["sync.py"]),
              patch.object(sync, "access_token", return_value="FAKE_TOKEN"),
              patch.object(sync, "api", return_value={"subscriptions": []}),
              patch.object(sync, "folders_from_subscriptions", return_value=(["BOHUN_MEDIA"], 1)),
              patch.object(sync, "stream_items", return_value=iter([source_item()])),
              patch.object(sync, "CaptureBudget", return_value=content.CaptureBudget(1)),
              patch.object(sync, "import_items") as writer,
              contextlib.redirect_stdout(io.StringIO())):
            with self.assertRaisesRegex(RuntimeError, "CAPTURE_METADATA_BUDGET_EXHAUSTED"):
                sync.main()
            writer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
