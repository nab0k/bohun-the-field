"""Independent collection cycles, exact-origin readback, and stale/duplicate receipts."""
import contextlib
from test_discovery import Offline, stamp, Problem
from collectors.source_registry.discovery.health import evaluate, read_delivery


class DeliveryCycles(Offline):
    def connection(self):
        store = self.state(); profile = self.profile(store)
        profile["lifecycle"] = "CONNECTED"
        profile["connection"] = {"membership_verified": True, "folder_verified": True,
            "connected_at": self.clock.now() - 100, "folder": "BOHUN_MEDIA"}
        return profile

    def read(self, profile, rows):
        self.queries = []
        outer = self
        class Cursor:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def execute(self, sql, params=None): outer.queries.append((sql, params))
            def fetchone(self): return ("Inoreader BOHUN", "https://www.inoreader.com/")
            def fetchmany(self, n):
                outer.assertEqual(n, 20); return rows
        class Connection:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def transaction(self): return contextlib.nullcontext()
            def cursor(self): return Cursor()
        return read_delivery(Connection, {"transport_source_id": 2}, profile)

    def test_two_distinct_cycles_after_connection_prove_active_once(self):
        p = self.connection(); origin = "feed/" + p["feed_url"]
        rows = [("a", stamp(self.clock.now()-20), origin, "BOHUN_MEDIA", "inoreader:" + "a"*32)]
        receipt = self.read(p, rows)
        p["delivery"] = evaluate(p, receipt, self.clock.now())
        self.assertEqual(p["delivery"]["lifecycle"], "CONNECTED")
        rows.append(("b", stamp(self.clock.now()-10), origin, "BOHUN_MEDIA", "inoreader:" + "b"*32))
        p["delivery"] = evaluate(p, self.read(p, rows), self.clock.now())
        self.assertEqual(p["delivery"]["lifecycle"], "ACTIVE")
        replay = evaluate(p, self.read(p, rows), self.clock.now())
        self.assertEqual(len(replay["cycles"]), 2)
        self.assertEqual(self.queries[0][0], "SET TRANSACTION READ ONLY")
        self.assertIn("raw_data->>'collector_cycle_id'", self.queries[-1][0])
        self.assertIn(origin, self.queries[-1][1])

    def test_same_cycle_different_saved_times_and_old_rows_do_not_prove_active(self):
        p = self.connection(); origin = "feed/" + p["feed_url"]
        rows = [("a", stamp(self.clock.now()-30), origin, "BOHUN_MEDIA", "inoreader:"+"a"*32),
                ("b", stamp(self.clock.now()-10), origin, "BOHUN_MEDIA", "inoreader:"+"a"*32),
                ("old", stamp(self.clock.now()-200), origin, "BOHUN_MEDIA", "inoreader:"+"b"*32)]
        result = evaluate(p, self.read(p, rows), self.clock.now())
        self.assertEqual(result["lifecycle"], "CONNECTED"); self.assertEqual(len(result["cycles"]), 1)
        result = evaluate(p, self.read(p, [("a", stamp(self.clock.now()-10), origin, "BOHUN_MEDIA", None)]), self.clock.now())
        self.assertEqual(result["state"], "FIRST_DELIVERY_PROVEN")
        self.assertEqual(result["cycles"], [])

    def test_wrong_origin_or_folder_does_not_establish_delivery(self):
        p = self.connection()
        receipt = self.read(p, [("a", stamp(self.clock.now()-10), "feed/https://other.example/rss", "BOHUN_MEDIA", "x"),
                                ("b", stamp(self.clock.now()-5), "feed/"+p["feed_url"], "BOHUN_GOV", "y")])
        self.assertEqual(evaluate(p, receipt, self.clock.now())["state"], "WAITING_DELIVERY")

    def test_invalid_stored_cycle_id_stays_unknown(self):
        p = self.connection(); origin = "feed/"+p["feed_url"]
        receipt = self.read(p, [("a", stamp(self.clock.now()-10), origin, "BOHUN_MEDIA", "legacy-or-provider-id")])
        self.assertIsNone(receipt["records"][0]["collector_cycle_id"])
        self.assertEqual(evaluate(p, receipt, self.clock.now())["cycles"], [])

    def test_later_batch_of_same_cycle_extends_interval_before_active_decision(self):
        p = self.connection(); origin = "feed/"+p["feed_url"]
        rows = [("a", stamp(self.clock.now()-30), origin, "BOHUN_MEDIA", "inoreader:"+"a"*32)]
        p["delivery"] = evaluate(p, self.read(p, rows), self.clock.now())
        rows.extend([("b", stamp(self.clock.now()-20), origin, "BOHUN_MEDIA", "inoreader:"+"b"*32),
                     ("c", stamp(self.clock.now()-10), origin, "BOHUN_MEDIA", "inoreader:"+"a"*32)])
        result = evaluate(p, self.read(p, rows), self.clock.now())
        self.assertEqual(result["lifecycle"], "CONNECTED")
