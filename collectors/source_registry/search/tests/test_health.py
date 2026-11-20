"""A6SH-02 health and usefulness without network or database."""
import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import health as H  # noqa: E402
import learning as L  # noqa: E402

NOW = dt.datetime(2026, 11, 28, 6, 0, tzinfo=dt.timezone.utc)
OLD = NOW - dt.timedelta(days=10)


def polls(ch, spec):
    """spec newest first: 'ok', 'new' (ok with new items), 'err', '304'."""
    out = []
    for i, s in enumerate(spec):
        at = NOW - dt.timedelta(hours=2 * i)
        out.append((ch, at, {"ok": 200, "new": 200, "err": None, "304": 304}[s], "http_500" if s == "err" else None,
                    3 if s == "new" else 0))
    return out


class Health(unittest.TestCase):
    def test_statuses(self):
        channels = [("new", "https://n.example/feed", NOW - dt.timedelta(days=1)),
                    ("fail", "https://f.example/feed", OLD), ("silent", "https://s.example/feed", OLD),
                    ("useless", "https://u.example/feed", OLD), ("good", "https://g.example/feed", OLD)]
        p = (polls("new", ["new"] * 4) + polls("fail", ["err", "err", "err", "new"]) + polls("silent", ["304"] * 5)
             + polls("useless", ["new", "ok", "304"]) + polls("good", ["304", "new", "ok"]))
        items = {"useless": (25, 0), "good": (12, 4)}
        rows = {r["channel_id"]: r for r in H.assess(channels, p, items, NOW)}
        self.assertEqual({k: v["status"] for k, v in rows.items()},
                         {"new": "new", "fail": "failing", "silent": "silent", "useless": "useless", "good": "healthy"})
        self.assertEqual(rows["fail"]["errors_streak"], 3)
        self.assertEqual((rows["good"]["usefulness"], rows["useless"]["usefulness"]), (0.333, 0.0))
        self.assertEqual({k for k, v in rows.items() if v["action"] == "recheck"}, {"fail", "silent"})

    def test_usefulness_weights_the_learning(self):
        q = {"types": 18, "layer1": 24, "layer3": 10, "section": 8}
        g = {"types": 14, "layer1": 16, "layer3": 10, "section": 0}
        hist = [{"at": NOW - dt.timedelta(hours=i), "layer": l, "country": "PL", "type_hint": "media",
                 "new_sites": 1, "accepted": 1} for l in ("types", "layer1") for i in range(15)]
        base, _, _ = L.adapt(q, g, hist, [], now=NOW)
        weighted, _, info = L.adapt(q, g, hist, [], now=NOW, useful={"types": (10, 10), "layer1": (10, 0)})
        self.assertGreater(weighted["types"], base["types"])
        self.assertLess(weighted["layer1"], base["layer1"])
        self.assertEqual(sum(weighted.values()), sum(q.values()))
        self.assertEqual(info["usefulness_weight"], {"types": 1.5, "layer1": 0.5})


if __name__ == "__main__":
    unittest.main()
