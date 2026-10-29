"""Offline streaming/partial-commit contracts. No credentials or external calls."""
import contextlib
import copy
import hashlib
import io
import sys
import types
import unittest
from unittest.mock import patch

from collectors.inoreader import sync
from collectors.inoreader.content_contract import capture_size


def records(count, html="<p>Body text</p>"):
    for i in range(count):
        yield sync.normalize({"id": str(i), "title": "Example", "summary": {"content": html}}, "BOHUN_TEST")


class Batches(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.network = patch("urllib.request.urlopen", side_effect=AssertionError("OFFLINE ONLY"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        self.redirect.__exit__(None, None, None)

    def test_eighty_bodies_preserved_with_bounded_live_buffer(self):
        html = "<!--" + "x" * 750000 + "--><p>" + "article " * 90 + "</p>"
        saved = []
        sizes = []
        produced = 0

        def source():
            nonlocal produced
            for item in records(80, html):
                produced += 1
                # At most one lookahead item beyond a six-item byte-bounded batch.
                self.assertLessEqual(produced - len(saved), 7)
                yield item

        def writer(batch):
            sizes.append(sum(capture_size(x["raw_data"]["content_capture"]) for x in batch))
            for item in batch:
                c = item["raw_data"]["content_capture"]
                self.assertEqual(c["raw_html"], html)
                self.assertEqual(c["raw_html_sha256"], hashlib.sha256(html.encode()).hexdigest())
                self.assertEqual(c["completeness"], "UNKNOWN")
                saved.append(item["external_id"])
            return len(batch), len(saved)

        stats = sync.process_batches(source(), writer)
        self.assertEqual(saved, [str(i) for i in range(80)])
        self.assertEqual(stats["new"], 80)
        self.assertGreater(stats["bytes"], 50 * 1024 * 1024)
        self.assertTrue(all(n <= sync.BATCH_CAPTURE_BYTES for n in sizes))
        self.assertEqual(stats["statuses"], {"BODY_PRESENT_COMPLETENESS_UNVERIFIED": 80})

    def test_count_boundary_and_final_partial_batch(self):
        lengths = []
        def writer(batch):
            lengths.append(len(batch))
            return len(batch), sum(lengths)
        stats = sync.process_batches(records(101), writer)
        self.assertEqual(lengths, [50, 50, 1])
        self.assertEqual(stats["committed_items"], 101)

    def test_byte_boundary_exact_fit_and_oversized_single(self):
        items = list(records(3))
        size = capture_size(items[0]["raw_data"]["content_capture"])
        # Capture timestamps have fixed width; keep identical for exact byte boundary.
        items = [copy.deepcopy(items[0]) for _ in range(3)]
        lengths = []
        def writer(batch):
            lengths.append(len(batch))
            return 0, 0
        sync.process_batches(iter(items), writer, max_capture_bytes=size * 2)
        self.assertEqual(lengths, [2, 1])
        lengths.clear()
        sync.process_batches(iter(items), writer, max_capture_bytes=1)
        self.assertEqual(lengths, [1, 1, 1])
        self.assertTrue(all(x["raw_data"]["content_capture"]["text"] for x in items))

    def test_failure_is_partial_and_repeat_preserves_existing_hashes(self):
        database = {}
        calls = 0
        def writer(batch):
            nonlocal calls
            calls += 1
            # Model an atomic transaction: changes become visible only on success.
            transaction = copy.deepcopy(database)
            new = 0
            for item in batch:
                key = item["external_id"]
                if key not in transaction:
                    transaction[key] = item["raw_data"]["content_capture"]["text_sha256"]
                    new += 1
            if calls == 2:
                raise RuntimeError("PRIVATE_BODY_URL_PASSWORD_SENTINEL")
            database.update(transaction)
            return new, len(database)
        with self.assertRaisesRegex(RuntimeError, "committed_batches=1 committed_items=2") as error:
            sync.process_batches(records(5), writer, max_items=2)
        self.assertNotIn("PRIVATE", str(error.exception))
        before = dict(database)
        self.assertEqual(set(before), {"0", "1"})
        stats = sync.process_batches(records(5), writer, max_items=2)
        self.assertEqual(stats["new"], 3)
        self.assertEqual(len(database), 5)
        self.assertEqual({key: database[key] for key in before}, before)
        self.assertEqual(sync.process_batches(records(5), writer)["new"], 0)

    def test_source_failure_reports_previous_commits_and_does_not_flush_pending(self):
        saved = []
        def source():
            yield from records(3)
            raise RuntimeError("secret")
        def writer(batch):
            saved.extend(x["external_id"] for x in batch)
            return len(batch), len(saved)
        with self.assertRaisesRegex(RuntimeError, "committed_items=2"):
            sync.process_batches(source(), writer, max_items=2)
        self.assertEqual(saved, ["0", "1"])

    def test_empty_dry_run_and_invalid_limits(self):
        self.assertEqual(sync.process_batches(iter(()))["batches"], 0)
        self.assertEqual(sync.process_batches(records(3), max_items=1)["committed_items"], 0)
        for value in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                sync.process_batches(iter(()), max_items=value)
            with self.assertRaises(ValueError):
                sync.process_batches(iter(()), max_capture_bytes=value)

    def test_real_import_function_commits_each_batch_and_rolls_back_failed_one(self):
        database = set()
        connections = []

        class Connection:
            def __init__(self):
                self.pending = set(database)
                self.row = None
                self.committed = False
                self.rolled_back = False
                connections.append(self)

            def __enter__(self):
                return self

            def __exit__(self, kind, *_):
                if kind is None:
                    database.update(self.pending)
                    self.committed = True
                else:
                    self.rolled_back = True

            @contextlib.contextmanager
            def transaction(self):
                yield

            @contextlib.contextmanager
            def cursor(self):
                yield self

            def execute(self, sql, params=()):
                if "to_regclass" in sql:
                    self.row = ("sources", "items")
                elif "SELECT id FROM intelligence.sources" in sql:
                    self.row = (2,)
                elif "INSERT INTO intelligence.items" in sql:
                    key = params[1]
                    if len(connections) == 2 and key == "3":
                        raise RuntimeError("fake transaction failed after one staged insert")
                    self.row = None if key in self.pending else (1,)
                    self.pending.add(key)
                elif "SELECT count(*)" in sql:
                    self.row = (len(self.pending),)
                else:
                    raise AssertionError(sql)

            def fetchone(self):
                return self.row

        psycopg = types.ModuleType("psycopg")
        psycopg.connect = lambda **_: Connection()
        json_module = types.ModuleType("psycopg.types.json")
        json_module.Jsonb = lambda value: value
        with patch.dict(sys.modules, {"psycopg": psycopg, "psycopg.types.json": json_module}), \
                patch.dict("os.environ", {"SUPABASE_DB_PASSWORD": "FAKE_OFFLINE"}):
            with self.assertRaisesRegex(RuntimeError, "committed_items=2"):
                sync.process_batches(records(5), sync.import_items, max_items=2)
            self.assertEqual(database, {"0", "1"})
            self.assertTrue(connections[0].committed)
            self.assertTrue(connections[1].rolled_back)
            stats = sync.process_batches(records(5), sync.import_items, max_items=2)
            self.assertEqual(stats["new"], 3)
            self.assertEqual(database, {str(i) for i in range(5)})


if __name__ == "__main__":
    unittest.main()
