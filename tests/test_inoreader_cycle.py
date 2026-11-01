"""Collector cycle provenance across streaming, batching and repeated runs, offline."""
import contextlib
import copy
import io
import unittest
from unittest.mock import patch

from collectors.inoreader import sync
from collectors.inoreader.body_backfill import backfill_missing_body, BACKFILL_SQL


class CycleProvenance(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch("urllib.request.urlopen", side_effect=AssertionError("OFFLINE_ONLY")))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def run_items(self):
        rows = [{"id": str(i), "title": "Example", "summary": {"content": "<p>Body</p>"},
                 "collector_cycle_id": "FORGED_BY_PROVIDER", "raw_data": {"collector_cycle_id": "FORGED"}}
                for i in range(3)]
        before = copy.deepcopy(rows)
        with patch.object(sync, "stream_items", side_effect=lambda *_: iter(rows)):
            result = list(sync.normalized_items(["BOHUN_MEDIA", "BOHUN_GOV"], "SYNTHETIC", 1))
        self.assertEqual(rows, before)
        return result

    def test_one_uuid_for_all_folders_and_batches_distinct_on_next_collection(self):
        first = self.run_items(); second = self.run_items()
        ids = {r["raw_data"]["collector_cycle_id"] for r in first}
        self.assertEqual(len(first), 3); self.assertEqual(len(ids), 1)
        self.assertRegex(next(iter(ids)), r"^inoreader:[0-9a-f]{32}$")
        self.assertTrue(ids.isdisjoint({r["raw_data"]["collector_cycle_id"] for r in second}))
        written = []
        sync.process_batches(iter(first), lambda batch: (written.extend(copy.deepcopy(batch)) or 0, 3), max_items=1)
        self.assertEqual({r["raw_data"]["collector_cycle_id"] for r in written}, ids)
        self.assertTrue(all(r["raw_data"]["content_capture"]["text"] for r in written))

    def test_direct_normalization_never_trusts_provider_id_or_invents_old_cycle(self):
        raw = {"id": "one", "collector_cycle_id": "FORGED"}
        self.assertNotIn("collector_cycle_id", sync.normalize(raw, "BOHUN_MEDIA")["raw_data"])
        for bad in (True, "forged", "inoreader:" + "Z" * 32):
            with self.assertRaises(ValueError): sync.normalize(raw, "BOHUN_MEDIA", collector_cycle_id=bad)

    def test_same_id_replay_keeps_first_delivery_cycle_at_existing_conflict_boundary(self):
        database = {}
        def writer(batch):
            new = 0
            for row in batch:
                if row["external_id"] not in database:
                    database[row["external_id"]] = copy.deepcopy(row); new += 1
            return new, len(database)
        first = sync.process_batches(iter(self.run_items()), writer)
        cycles = {r["raw_data"]["collector_cycle_id"] for r in database.values()}
        replay = sync.process_batches(iter(self.run_items()), writer)
        self.assertEqual(first["new"], 3); self.assertEqual(replay["new"], 0)
        self.assertEqual({r["raw_data"]["collector_cycle_id"] for r in database.values()}, cycles)

    def test_body_backfill_does_not_pass_new_cycle_as_delivery_provenance(self):
        row = self.run_items()[0]; calls = []
        class Cursor:
            def execute(self, sql, values): calls.append((sql, values))
            def fetchone(self): return (1,)
        self.assertTrue(backfill_missing_body(Cursor(), 2, row, lambda x: x))
        self.assertEqual(calls[0][0], BACKFILL_SQL)
        self.assertNotIn(row["raw_data"]["collector_cycle_id"], str(calls[0][1]))
        self.assertIn("jsonb_set(raw_data,'{content_capture}'", BACKFILL_SQL)
