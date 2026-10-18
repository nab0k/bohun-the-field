"""Offline acceptance checks for a bounded runner reusing INPUT-02 unchanged."""
import contextlib
import copy
import io
import json
import subprocess
import types
import unittest
from unittest.mock import patch

from collectors.inoreader import probe
from collectors.inoreader import sync
from collectors.inoreader.content_contract import CaptureBudget


SHA = "a" * 40
SNAPSHOT = dict(rls=True, exposure=[("anon", False, False, False),
                                   ("authenticated", False, False, False)],
                columns=[("raw_data", "jsonb", "YES", None)], triggers=[],
                wire=[("reviewed title/url/date projection", True, True, True)])


def raw(key="one", body="<p>PRIVATE_BODY_SENTINEL</p>"):
    result = {"id": key, "title": "PRIVATE_TITLE_SENTINEL", "published": 1792238400,
              "canonical": [{"href": "https://private.example.test/news"}],
              "origin": {"title": "Example", "streamId": "feed/example"}}
    if body is not None:
        result["summary"] = {"content": body}
    return result


def api_fixture(items=None, folder_count=1):
    items = [raw()] if items is None else items
    subscriptions = [{"categories": [{"label": f"BOHUN_{i:02}"}]} for i in range(folder_count)]
    def api(path, token, params=None):
        if path == "subscription/list":
            return {"subscriptions": subscriptions}
        return {"items": copy.deepcopy(items), "continuation": "MUST_NOT_FOLLOW"}
    return api


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.snapshot = copy.deepcopy(SNAPSHOT)
        self.calls = []
        self.source_count = 1
        self.mismatch = False
        self.reopen_failure = False
        self.commit_failure = False
        self.race = False
        self.commits = 0
        self.rollbacks = 0
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @contextlib.contextmanager
    def transaction(self, *, readonly=False):
        before = copy.deepcopy(self.rows)
        self.readonly = readonly
        try:
            yield
        except Exception:
            self.rows = before
            self.rollbacks += 1
            raise
        else:
            if not readonly:
                self.commits += 1
                if self.commit_failure:
                    raise RuntimeError("PRIVATE_COMMIT_ACK_SENTINEL")

    def inspect(self):
        return copy.deepcopy(self.snapshot)

    def source_id(self):
        if self.source_count != 1:
            raise probe.ProbeError("SOURCE_MISSING_OR_AMBIGUOUS")
        return "source"

    def existing(self, source, ids):
        self.reads += 1
        if self.race and self.reads > 1:
            return set(ids)
        return set(ids) & self.rows.keys()

    def insert(self, source, item):
        if self.readonly:
            raise AssertionError("Write in read-only transaction")
        self.calls.append(item["external_id"])
        if item["external_id"] in self.rows:
            return False
        self.rows[item["external_id"]] = copy.deepcopy(item)
        return True

    def read(self, source, ids):
        rows = {key: copy.deepcopy(self.rows[key]) for key in ids if key in self.rows}
        if self.mismatch and rows:
            rows[next(iter(rows))]["title"] = "DATABASE_CHANGED_VALUE"
        return rows

    def reopen(self):
        if self.reopen_failure:
            raise RuntimeError("PRIVATE_DB_PASSWORD_SENTINEL")
        return self


class OfflineProbeTests(unittest.TestCase):
    def setUp(self):
        guard = patch("urllib.request.urlopen", side_effect=AssertionError("Unexpected live network"))
        guard.start()
        self.addCleanup(guard.stop)

    def preview(self, store, api=None):
        with patch.object(sync, "api", side_effect=api or api_fixture()):
            return probe.run_probe(store, "PRIVATE_TOKEN_SENTINEL")

    def apply(self, store, api=None, preview=None):
        preview = preview or self.preview(store, api)
        with patch.object(sync, "api", side_effect=api or api_fixture()):
            return probe.run_probe(store, "PRIVATE_TOKEN_SENTINEL", apply=True,
                                   expected_db=preview["db_fingerprint"],
                                   expected_batch=preview["batch_fingerprint"])

    def test_reuses_existing_capture_and_normalizer(self):
        store = FakeStore()
        with patch.object(sync, "normalize", wraps=sync.normalize) as normalize:
            self.preview(store)
        self.assertEqual(normalize.call_count, 1)
        self.assertIsInstance(normalize.call_args.kwargs["content_budget"], CaptureBudget)

    def test_preview_never_writes_and_does_not_log_payload(self):
        store = FakeStore()
        result = self.preview(store)
        self.assertEqual(result["result"], "PREVIEW_ONLY")
        self.assertEqual(store.calls, [])
        self.assertEqual(store.commits, 0)
        serialized = json.dumps(result)
        for forbidden in ["PRIVATE_", "private.example", "raw_html", "captured_at"]:
            self.assertNotIn(forbidden, serialized)

    def test_three_pages_at_most_twenty_each_and_no_continuation(self):
        api = api_fixture([raw(str(i)) for i in range(20)], folder_count=10)
        with patch.object(sync, "api", side_effect=api) as call:
            unique, result = probe.collect("token")
        self.assertEqual(result, {"folders": 3, "fetched": 60, "unique": 20})
        self.assertEqual(len(unique), 20)
        self.assertEqual(call.call_count, 4)
        self.assertTrue(all(c.args[2] == {"n": 20} for c in call.call_args_list[1:]))

    def test_provider_oversized_page_aborts_without_write(self):
        store = FakeStore()
        with self.assertRaisesRegex(probe.ProbeError, "PAGE_LIMIT"):
            self.preview(store, api_fixture([raw(str(i)) for i in range(21)]))
        self.assertEqual(store.calls, [])

    def test_missing_folder_or_bad_id_fails(self):
        for api in [api_fixture(folder_count=0), api_fixture([{"id": 42}])]:
            with self.subTest(api=api), self.assertRaises(probe.ProbeError):
                self.preview(FakeStore(), api)

    def test_at_most_five_new_keys_known_and_duplicates_not_written(self):
        api = api_fixture([raw(str(i)) for i in range(12)] + [raw("0")], folder_count=3)
        store = FakeStore()
        old = sync.normalize(raw("0", None), "OLD_FOLDER")
        store.rows["0"] = copy.deepcopy(old)
        result = self.apply(store, api)
        self.assertEqual(result["inserted"], 5)
        self.assertEqual(len(store.calls), 10)  # Five inserts + same-key replay.
        self.assertEqual(len(set(store.calls)), 5)
        self.assertNotIn("0", store.calls)
        self.assertEqual(store.rows["0"], old)

    def test_body_first_and_capture_not_misrepresented_as_complete(self):
        api = api_fixture([raw("metadata", None), raw("body")])
        store = FakeStore()
        self.apply(store, api)
        self.assertEqual(store.calls[:2], ["body", "metadata"])
        for item in store.rows.values():
            capture = item["raw_data"]["content_capture"]
            self.assertEqual(capture["completeness"], "UNKNOWN")
            self.assertNotIn("public_release", capture)
        self.assertEqual(store.rows["body"]["raw_data"]["content_capture"]["raw_html"],
                         raw()["summary"]["content"])
        self.assertEqual(store.rows["metadata"]["raw_data"]["content_capture"]["status"], "METADATA_ONLY")

    def test_replay_same_batch_zero_inserts_and_verified_readback(self):
        store = FakeStore()
        result = self.apply(store)
        self.assertEqual(result["result"], "VERIFIED_BODY_CAPTURE")
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["replay_inserted"], 0)
        self.assertEqual(store.calls, ["one", "one"])
        self.assertEqual(store.commits, 1)

    def test_no_new_body_is_inconclusive_and_no_backfill(self):
        for api in [api_fixture([]), api_fixture([raw(body=None)])]:
            result = self.apply(FakeStore(), api)
            self.assertEqual(result["result"], "INCONCLUSIVE_NO_NEW_BODY")

    def test_batch_or_catalog_changes_abort_before_insert(self):
        for field in ["db_fingerprint", "batch_fingerprint"]:
            store = FakeStore()
            preview = self.preview(store)
            preview[field] = "b" * 64
            with self.subTest(field=field), self.assertRaisesRegex(probe.ProbeError, "FINGERPRINT_MISMATCH"):
                self.apply(store, preview=preview)
            self.assertEqual(store.calls, [])

    def test_missing_apply_guards_fail_before_oauth_or_database(self):
        with patch.object(probe, "verify_revision"), patch.object(sync, "access_token") as token, \
                patch.object(probe, "open_store") as db:
            with self.assertRaisesRegex(probe.ProbeError, "FINGERPRINTS_REQUIRED"):
                probe.main(["--expected-sha", SHA, "--apply"])
        token.assert_not_called()
        db.assert_not_called()

    def test_missing_source_or_ambiguous_source_aborts(self):
        for count in [0, 2]:
            store = FakeStore()
            store.source_count = count
            with self.subTest(count=count), self.assertRaisesRegex(probe.ProbeError, "SOURCE_MISSING"):
                self.preview(store)
            self.assertEqual(store.calls, [])

    def test_public_exposure_disabled_rls_or_triggers_fail_closed(self):
        variants = [dict(rls=False), dict(triggers=[("unknown", "trigger")]),
                    dict(wire=[]), dict(exposure=[("anon", False, False, True)])]
        for variant in variants:
            store = FakeStore()
            store.snapshot.update(variant)
            with self.subTest(variant=variant), self.assertRaises(probe.ProbeError):
                self.preview(store)
            self.assertEqual(store.calls, [])

    def test_store_mismatch_rolls_back_entire_batch(self):
        store = FakeStore()
        preview = self.preview(store)
        store.mismatch = True
        with self.assertRaisesRegex(probe.ProbeError, "STORED_BATCH_MISMATCH"):
            self.apply(store, preview=preview)
        self.assertEqual(store.rows, {})
        self.assertEqual(store.commits, 0)
        self.assertEqual(store.rollbacks, 1)

    def test_conflict_race_skips_without_overwrite(self):
        store = FakeStore()
        preview = self.preview(store)
        store.reads = 0
        store.race = True
        result = self.apply(store, preview=preview)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(result["result"], "INCONCLUSIVE_NO_NEW_BODY")
        self.assertEqual(store.calls, [])

    def test_post_commit_failure_reports_uncertainty_and_never_retries(self):
        store = FakeStore()
        store.reopen_failure = True
        with self.assertRaisesRegex(probe.ProbeError, "POST_COMMIT_UNCONFIRMED_NO_RETRY") as error:
            self.apply(store)
        self.assertNotIn("PASSWORD", str(error.exception))
        self.assertEqual(store.calls, ["one", "one"])
        self.assertEqual(store.commits, 1)
        self.assertEqual(len(store.rows), 1)

    def test_lost_commit_ack_is_uncertain_not_a_claimed_rollback(self):
        store = FakeStore()
        store.commit_failure = True
        with self.assertRaisesRegex(probe.ProbeError, "COMMIT_UNCONFIRMED_NO_RETRY") as error:
            self.apply(store)
        self.assertNotIn("PRIVATE_", str(error.exception))
        self.assertEqual(store.calls, ["one", "one"])
        self.assertEqual(store.commits, 1)
        self.assertEqual(store.rollbacks, 0)

    def test_writer_limit_and_publication_fields_checked_before_insert(self):
        items = [sync.normalize(raw(str(i)), "BOHUN_A") for i in range(6)]
        with self.assertRaisesRegex(probe.ProbeError, "BATCH_LIMIT"):
            probe.validate_batch(items)
        items = items[:1]
        items[0]["raw_data"]["content_capture"]["public_release"] = True
        with self.assertRaisesRegex(probe.ProbeError, "PUBLICATION_FIELD"):
            probe.validate_batch(items)

    def test_capture_budget_and_oversize_are_existing_patch_statuses(self):
        api = api_fixture([raw("large", "x" * (2 * 1024 * 1024 + 1)),
                           raw("body")])
        result = self.preview(FakeStore(), api)
        self.assertEqual(result["statuses"]["TOO_LARGE"], 1)
        self.assertEqual(result["statuses"]["BODY_PRESENT_COMPLETENESS_UNVERIFIED"], 1)
        self.assertLessEqual(result["capture_bytes"], 5 * 1024 * 1024)

    def test_shared_capture_budget_is_enforced_across_selected_items(self):
        html = "<p>Short text</p><!--" + "x" * 1_900_000 + "-->"
        result = self.preview(FakeStore(), api_fixture([raw(str(i), html) for i in range(4)]))
        self.assertLessEqual(result["capture_bytes"], 5 * 1024 * 1024)
        self.assertGreater(result["statuses"].get("CAPTURE_BUDGET_EXHAUSTED", 0), 0)

    def test_preview_apply_batch_identity_ignores_only_receipt_time(self):
        first = sync.normalize(raw(), "BOHUN_A")
        second = copy.deepcopy(first)
        second["raw_data"]["content_capture"]["captured_at"] = "2026-10-19T12:00:00+00:00"
        self.assertEqual(probe.batch_fingerprint([first]), probe.batch_fingerprint([second]))
        second["raw_data"]["origin_title"] = "Changed publisher"
        self.assertNotEqual(probe.batch_fingerprint([first]), probe.batch_fingerprint([second]))

    def test_main_preview_outputs_only_sanitized_summary(self):
        store = FakeStore()
        output = io.StringIO()
        with patch.object(probe, "verify_revision"), \
                patch.object(sync, "access_token", return_value="PRIVATE_TOKEN_SENTINEL"), \
                patch.object(sync, "api", side_effect=api_fixture()), \
                patch.object(probe, "open_store", return_value=store), contextlib.redirect_stdout(output):
            code = probe.main(["--expected-sha", SHA])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["mode"], "preview")
        self.assertNotIn("PRIVATE_", output.getvalue())

    def test_revision_branch_and_clean_tree_guards(self):
        for values in [[SHA, probe.BRANCH, "collectors/inoreader/probe.py", ""],
                       ["b" * 40, probe.BRANCH, "probe.py", ""],
                       [SHA, "main", "probe.py", ""],
                       [SHA, probe.BRANCH, "probe.py", " M sync.py"]]:
            with self.subTest(values=values), patch.dict("os.environ", {"GITHUB_ACTIONS": "false"}), \
                    patch.object(subprocess, "check_output", side_effect=[v.encode() for v in values]):
                if values[0] == SHA and values[1] == probe.BRANCH and not values[-1]:
                    probe.verify_revision(SHA)
                else:
                    with self.assertRaises(probe.ProbeError):
                        probe.verify_revision(SHA)


class WorkflowGuardTests(unittest.TestCase):
    def test_feature_workflow_has_no_automatic_trigger_or_full_sync(self):
        workflow = (probe.REPO / ".github/workflows/inoreader-cloud.yml").read_text()
        self.assertNotIn("schedule:", workflow)
        self.assertNotIn("  push:", workflow)
        self.assertNotIn("sync.py --max-pages", workflow)
        self.assertIn("default: preview", workflow)
        self.assertIn("github.ref == 'refs/heads/" + probe.BRANCH + "'", workflow)
        self.assertIn("timeout-minutes: 10", workflow)
        self.assertIn("group: bohun-inoreader-cloud", workflow)


class PostgresAdapterTests(unittest.TestCase):
    def test_parameterized_conflict_insert_never_updates_or_creates_source(self):
        from unittest.mock import MagicMock
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [("new-id",), None]
        store = probe.PostgresStore(conn, lambda: None)
        item = sync.normalize(raw(), "BOHUN_A")
        json_module = types.ModuleType("psycopg.types.json")
        json_module.Jsonb = lambda value: value
        with patch.dict("sys.modules", {"psycopg.types.json": json_module}):
            self.assertTrue(store.insert("source-id", item))
            self.assertFalse(store.insert("source-id", item))
        for call in conn.execute.call_args_list:
            sql, params = call.args
            self.assertIn("ON CONFLICT (source_id, external_id) DO NOTHING", sql)
            self.assertNotIn("UPDATE", sql)
            self.assertNotIn("intelligence.sources", sql)
            self.assertNotIn(item["title"], sql)
            self.assertEqual(params[-1], item["raw_data"])

    def test_real_adapter_requires_exactly_one_existing_source(self):
        from unittest.mock import MagicMock
        for rows in [[], [("one",), ("two",)], [("only",)]]:
            conn = MagicMock()
            conn.execute.return_value.fetchall.return_value = rows
            store = probe.PostgresStore(conn, lambda: None)
            if len(rows) == 1:
                self.assertEqual(store.source_id(), "only")
            else:
                with self.assertRaisesRegex(probe.ProbeError, "SOURCE_MISSING"):
                    store.source_id()
            self.assertTrue(conn.execute.call_args.args[0].startswith("SELECT"))

    def test_readback_maps_exact_columns_and_refuses_changed_item_type(self):
        from unittest.mock import MagicMock
        item = sync.normalize(raw(), "BOHUN_A")
        conn = MagicMock()
        row = tuple(item[k] for k in ("external_id", "title", "url", "published_at", "raw_data"))
        conn.execute.return_value.fetchall.return_value = [(*row, "news")]
        store = probe.PostgresStore(conn, lambda: None)
        self.assertEqual(store.read("source", ["one"]), {"one": item})
        conn.execute.return_value.fetchall.return_value = [(*row, "analysis")]
        with self.assertRaisesRegex(probe.ProbeError, "ITEM_TYPE_MISMATCH"):
            store.read("source", ["one"])

    def test_readonly_transaction_and_error_propagate_to_rollback_context(self):
        from unittest.mock import MagicMock
        conn = MagicMock()
        store = probe.PostgresStore(conn, lambda: None)
        with self.assertRaisesRegex(probe.ProbeError, "synthetic"):
            with store.transaction(readonly=True):
                raise probe.ProbeError("synthetic")
        statements = [c.args[0] for c in conn.execute.call_args_list]
        self.assertEqual(statements[0], "SET TRANSACTION READ ONLY")
        self.assertIn("statement_timeout", statements[1])
        exit_call = conn.transaction.return_value.__exit__.call_args
        self.assertIs(exit_call.args[0], probe.ProbeError)


if __name__ == "__main__":
    unittest.main()
