"""A1SD-03 self-improvement without network: learning rows, quotas by yield, gaps, sleeping cells, reset, streak."""
import datetime as dt
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import learning as L  # noqa: E402

NOW = dt.datetime(2026, 11, 20, 5, 0, tzinfo=dt.timezone.utc)
QUOTAS = {"types": 18, "layer1": 24, "layer3": 10, "section": 8, "gdelt": 0, "wikidata": 20}
GEMINI = {"types": 14, "layer1": 16, "layer3": 10, "section": 0}


def hist(layer, n, accepted_each, country="PL", new=1, days_ago=1):
    return [{"at": NOW - dt.timedelta(days=days_ago, minutes=i), "layer": layer, "country": country,
             "type_hint": "media", "new_sites": new, "accepted": accepted_each} for i in range(n)]


class Learning(unittest.TestCase):
    def test_rows_count_decisions_per_job(self):
        stats = {"types|PL||procurement|en|": {"layer": "types", "engine": "tavily", "country": "PL",
                                               "type_hint": "procurement", "results": 8, "new": 2},
                 "layer1|DE|ew||de|": {"layer": "layer1", "engine": "gemini", "country": "DE", "type_hint": None,
                                       "results": 3, "new": 0}}
        results = [{"decision": "accept", "found_via_job": "types|PL||procurement|en|"},
                   {"decision": "hold", "found_via_job": "types|PL||procurement|en|"},
                   {"decision": "accept", "found_via_job": None}]
        rows = {r["job_key"]: r for r in L.rows_for_run("r1", stats, results)}
        self.assertEqual((rows["types|PL||procurement|en|"]["accepted"], rows["types|PL||procurement|en|"]["held"]), (1, 1))
        self.assertEqual((rows["layer1|DE|ew||de|"]["new_sites"], rows["layer1|DE|ew||de|"]["accepted"]), (0, 0))

    def test_defaults_until_enough_history(self):
        q, g, info = L.adapt(QUOTAS, GEMINI, hist("types", 5, 1), [], now=NOW)
        self.assertEqual((q, g, info["mode"]), (QUOTAS, GEMINI, "default"))

    def test_quotas_follow_yield_with_floor_and_same_total(self):
        h = hist("types", 20, 1) + hist("layer1", 20, 0) + hist("layer3", 10, 0)
        q, g, info = L.adapt(QUOTAS, GEMINI, h, [], now=NOW)
        search = ("types", "layer1", "layer3", "section")
        self.assertEqual(info["mode"], "learned")
        self.assertEqual(sum(q[l] for l in search), sum(QUOTAS[l] for l in search))  # free limits never exceeded
        self.assertGreater(q["types"], QUOTAS["types"])
        self.assertLess(q["layer1"], QUOTAS["layer1"])
        floor = round(0.10 * sum(QUOTAS[l] for l in search))
        self.assertTrue(all(q[l] >= floor for l in search))
        self.assertTrue(all(g[l] <= q[l] for l in GEMINI))
        self.assertEqual((q["wikidata"], q["gdelt"]), (20, 0))     # open bases keep their fixed share

    def test_old_history_is_ignored(self):
        q, _, info = L.adapt(QUOTAS, GEMINI, hist("types", 30, 1, days_ago=9), [], now=NOW)
        self.assertEqual(info["mode"], "default")

    def test_reset_when_share_falls_by_half(self):
        h = hist("types", 20, 1) + hist("layer1", 20, 0)
        runs = [(NOW, 0.1), (NOW, 0.5), (NOW, 0.4), (NOW, 0.6)]
        q, g, info = L.adapt(QUOTAS, GEMINI, h, runs, now=NOW)
        self.assertEqual((q, info["mode"]), (QUOTAS, "reset"))

    def test_sleeping_cells_and_gap_order(self):
        h = hist("types", 3, 0, country="FR", new=0) + hist("types", 3, 0, country="PL", new=1)
        asleep = L.sleeping_cells(h, now=NOW)
        self.assertEqual(asleep, {("types", "FR")})
        jobs = [{"layer": "types", "country": c, "type_hint": t} for c, t in
                (("FR", "media"), ("PL", "media"), ("DE", "grants"), ("DE", "media"))]
        ordered = L.order_by_gaps(jobs, {"PL": 5, "DE": 0}, {"media": 9, "grants": 0}, asleep)
        self.assertEqual([(j["country"], j["type_hint"]) for j in ordered],
                         [("DE", "grants"), ("DE", "media"), ("PL", "media"), ("FR", "media")])

    def test_streak(self):
        self.assertEqual(L.streak([True, True, False, True], True), 3)
        self.assertEqual(L.streak([True, True], False), 0)
        self.assertEqual(L.streak([], True), 1)


if __name__ == "__main__":
    unittest.main()
