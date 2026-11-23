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
                    3 if s == "new" else 0, f"run-{i}"))
    return out


class Health(unittest.TestCase):
    def test_statuses(self):
        channels = [("new", "https://n.example/feed", NOW - dt.timedelta(days=1)),
                    ("fail", "https://f.example/feed", OLD), ("silent", "https://s.example/feed", OLD),
                    ("useless", "https://u.example/feed", OLD), ("good", "https://g.example/feed", OLD)]
        p = (polls("new", ["new"] * 4) + polls("fail", ["err", "err", "err", "new"]) + polls("silent", ["304"] * 5)
             + polls("useless", ["new", "ok", "304"]) + polls("good", ["304", "new", "ok"]))
        items = {"useless": (25, 0), "good": (12, 4)}
        rows = {r["channel_id"]: r for r in H.assess(channels, p, items, NOW, {c[0] for c in channels})}
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

    def test_transport_outage_preserves_admission(self):
        channels = [(channel, f"https://provider.example/{channel}", OLD) for channel in ("a", "b")]
        p = polls("a", ["err", "err", "err", "ok"]) + polls("b", ["err", "err", "err", "ok"])
        rows = H.assess(channels, p, {}, NOW, {"a", "b"})
        self.assertEqual({row["reason"] for row in rows}, {"transport_unconfirmed"})
        self.assertEqual({row["action"] for row in rows}, {None})

    def test_first_poll_reasons(self):
        channels = [(channel, f"https://provider.example/{channel}", since) for channel, since in (
            ("blocked", OLD), ("overdue", OLD), ("pending", NOW - dt.timedelta(hours=1)), ("peer", OLD))]
        eligible = {"overdue", "pending", "peer"}
        rows = H.assess(channels, polls("peer", ["new", "ok"]), {}, NOW, eligible)
        self.assertEqual({row["channel_id"]: row["reason"] for row in rows},
                         {"blocked": "not_eligible", "overdue": "first_poll_overdue",
                          "pending": "first_poll_pending", "peer": None})


if __name__ == "__main__":
    unittest.main()
