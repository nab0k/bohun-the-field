"""A4SC-02 / A5TC-03 YouTube without network or database: Data API calls and their cost, the key kept out of errors,
discovery rotation, channel admission (fresh uploads + judge), collection in the feed shape, the 30-day refresh,
and a spent quota stopping YouTube only."""
import contextlib
import datetime as dt
import io
import json
import os
import sys
import types
import unittest
import urllib.error
import urllib.parse

from collectors.feeds import youtube as Y
from tests.feeds.test_collect import NOW, Resp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "collectors", "source_registry", "search"))
fake_json = types.ModuleType("psycopg.types.json")
fake_json.Jsonb = lambda v: v
sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))
sys.modules.setdefault("psycopg.types", types.ModuleType("psycopg.types"))
sys.modules["psycopg.types.json"] = fake_json

UC = "UC" + "a" * 22


def iso(days_ago):
    return (NOW - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def playlist(videos):
    return {"items": [{"snippet": {"title": t, "description": d, "publishedAt": iso(a), "resourceId": {"videoId": v}},
                       "contentDetails": {"videoId": v, "videoPublishedAt": iso(a)}} for v, t, d, a in videos]}


class API:
    """Fake Data API: endpoint -> JSON (or a callable of params, or an HTTP error code)."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, req, timeout):
        u = urllib.parse.urlsplit(req.full_url)
        endpoint, params = u.path.rsplit("/", 1)[-1], dict(urllib.parse.parse_qsl(u.query))
        self.calls.append((endpoint, params))
        r = self.routes.get(endpoint, {"items": []})
        r = r(params) if callable(r) else r
        if isinstance(r, tuple):
            code, reason = r
            raise urllib.error.HTTPError(req.full_url, code, "x", {},
                                         io.BytesIO(json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()))
        return Resp(200, json.dumps(r).encode())


class Cur:
    def __init__(self, pg):
        self.pg, self.rows, self.rowcount = pg, [], 0

    def execute(self, sql, params=None):
        sql = " ".join(sql.split())
        self.pg.log.append((sql, params))
        self.rows = next((rows for key, rows in self.pg.reads.items() if key in sql), [])

    def fetchall(self):
        return self.rows


class PG:
    def __init__(self, reads=None):
        self.reads, self.log = reads or {}, []

    def cursor(self):
        return Cur(self)

    def transaction(self):
        return contextlib.nullcontext()


def judge_says(specialised):
    calls = []

    def judge(host, name, pairs, prompt=None):
        calls.append((host, len(pairs)))
        judge.prompts.append(prompt)
        return {"specialised": specialised, "type": "media", "defence_section": None, "confidence": 0.9}
    judge.calls, judge.prompts = calls, []
    return judge


CAT = {"countries": {"PL": {"lang": "pl"}, "DE": {"lang": "de"}},
       "categories": {"uncrewed": {"terms": {"en": "military drones", "pl": "drony wojskowe", "de": "Militärdrohnen"},
                                   "default_type": "media"},
                      "radar": {"terms": {"en": "military radar"}, "default_type": "media"}}}


class YouTube(unittest.TestCase):
    def test_client_cost_key_and_quota(self):
        api = API({"search": {"items": []}, "videos": (403, "quotaExceeded"), "channels": (404, "channelNotFound")})
        c = Y.Client("SECRET", opener=api)
        c.get("search", cost=100, q="x")
        self.assertEqual((c.units, api.calls[0][1]["key"]), (100, "SECRET"))
        self.assertIsNone(c.get("channels", id="x"))
        with self.assertRaises(Y.QuotaOut) as e:
            c.get("videos", id="x")
        self.assertNotIn("SECRET", str(e.exception) + json.dumps(c.errors))
        self.assertEqual(c.errors, {"channels:404:channelNotFound": 1, "videos:403:quotaExceeded": 1})

    def test_uploads_skip_private_and_sort(self):
        api = API({"playlistItems": playlist([("v1", "Old", "d", 5), ("v2", "Private video", "", 1),
                                              ("v3", "New", "desc", 1)])})
        got = Y.uploads(Y.Client("k", opener=api), UC)
        self.assertEqual([v["id"] for v in got], ["v3", "v1"])
        self.assertEqual(api.calls[0][1]["playlistId"], "UU" + UC[2:])

    def test_cells_rotate_and_use_country_language(self):
        day1, day2 = Y.cells(CAT, 1, 2), Y.cells(CAT, 2, 2)
        self.assertEqual(len(day1), 2)
        self.assertNotEqual(day1, day2)
        every = {(c["country"], c["category"]) for d in range(4) for c in Y.cells(CAT, d, 1)}
        self.assertEqual(len(every), 4)
        pl = next(c for d in range(4) for c in Y.cells(CAT, d, 1) if c["country"] == "PL" and c["category"] == "uncrewed")
        self.assertEqual((pl["q"], pl["lang"]), ("drony wojskowe", "pl"))
        de = next(c for d in range(4) for c in Y.cells(CAT, d, 1) if c["country"] == "DE" and c["category"] == "radar")
        self.assertEqual(de["q"], "military radar")  # a missing translation falls back to English

    def test_assess_needs_fresh_uploads_then_judge(self):
        fresh = [(f"v{i}", f"Title {i}", "", i) for i in range(6)]
        stale = [(f"v{i}", f"Title {i}", "", 200 + i) for i in range(6)]
        client = Y.Client("k", opener=API({"channels": {"items": [{"snippet": {"title": "Ch", "customUrl": "@ch"}}]},
                                           "playlistItems": playlist(fresh)}))
        yes, no = judge_says(True), judge_says(False)
        self.assertEqual(Y.assess(client, yes, UC, NOW)[:2], ("accept", "accepted"))
        self.assertEqual(yes.calls, [("youtube.com/@ch", 6)])
        self.assertEqual(yes.prompts, [Y.CHANNEL_PROMPT])  # the channel prompt, not the website one
        self.assertEqual(Y.assess(client, no, UC, NOW)[:2], ("reject", "rejected"))
        self.assertEqual(Y.assess(client, None, UC, NOW)[:2], ("hold", "on_hold"))
        quiet = Y.Client("k", opener=API({"playlistItems": playlist(stale)}))
        self.assertEqual(Y.assess(quiet, yes, UC, NOW)[:2], ("reject", "no_news"))
        record = Y.assess(client, yes, UC, NOW)[2]
        self.assertNotIn("Ch", json.dumps(record))  # no API titles kept in source_discovery

    def test_discover_skips_known_and_saves_to_source_discovery_only(self):
        other = "UC" + "b" * 22
        api = API({"search": {"items": [{"id": {"channelId": UC}}, {"id": {"channelId": other}}]},
                   "playlistItems": playlist([(f"v{i}", f"T{i}", "", i) for i in range(6)])})
        decisions = Y.discover(Y.Client("k", opener=api), judge_says(True), CAT, known={other}, now=NOW, n=2)
        self.assertEqual([d[0] for d in decisions], [UC])
        self.assertEqual(sum(1 for e, _ in api.calls if e == "search"), 2)
        pg = PG()
        self.assertEqual(Y.save_decisions(pg, "r1", decisions, NOW), {"accepted": 1})
        writes = [s for s, _ in pg.log if s.startswith("insert")]
        self.assertTrue(writes and all(s.startswith("insert into source_discovery.") for s in writes))
        state = next(p for s, p in pg.log if "source_state" in s)
        self.assertEqual((state[1], state[2]), (Y.channel_url(UC), "accepted"))
        self.assertIn("'youtube'", next(s for s, _ in pg.log if "source_state" in s))

    def test_collect_fresh_videos_with_description(self):
        api = API({"playlistItems": playlist([("v1", "New", "Army orders radars. " * 30, 1), ("v2", "Old", "x", 30)])})
        polls, shadow = Y.collect(Y.Client("k", opener=api),
                                  [{"channel_id": "ch_1", "url": Y.channel_url(UC)},
                                   {"channel_id": "ch_2", "url": "https://www.youtube.com/@handle"}], NOW)
        self.assertEqual([(p["channel_id"], p["fresh"], p["error"]) for p in polls],
                         [("ch_1", 1, None), ("ch_2", 0, "not_a_channel")])
        s = shadow[0]
        self.assertEqual((s["url"], s["title"]), ("https://www.youtube.com/watch?v=v1", "New"))
        self.assertGreater(s["text_chars"], 400)
        self.assertEqual(s["raw_extra"]["collector"], Y.COLLECTOR)
        self.assertEqual(s["raw_extra"]["youtube"]["video_id"], "v1")

    def test_refresh_updates_live_and_clears_gone(self):
        pg = PG({"select i.id": [(1, "v1"), (2, "v2")]})
        api = API({"videos": {"items": [{"id": "v1", "snippet": {"title": "New title", "description": "d" * 300}}]}})
        out = Y.refresh(pg, Y.Client("k", opener=api), "BOHUN feeds (A1SD)", NOW)
        self.assertEqual(out, {"refreshed": 1, "cleared": 1})
        self.assertEqual(api.calls[0][1]["id"], "v1,v2")
        ups = [(s, p) for s, p in pg.log if s.startswith("update intelligence.items")]
        self.assertEqual(ups[0][1][0], "New title")
        self.assertIn("(video removed)", ups[1][0])
        self.assertTrue(ups[1][1][0]["youtube"]["removed"])
        self.assertIsNone(ups[1][1][0]["content_capture"])

    def test_run_quota_stops_youtube_only(self):
        pg = PG({"status = 'accepted' and delivery = 'youtube'": [("ch_1", Y.channel_url(UC))]})
        api = API({"playlistItems": (403, "quotaExceeded")})
        polls, shadow, counts = Y.run(pg, NOW, "r1", "k", write=True, discover_now=False, source_name="S",
                                      client=Y.Client("k", opener=api))
        self.assertEqual((polls, shadow), ([], []))
        self.assertEqual(counts["stopped"], "playlistItems:403:quotaExceeded")
        ok = API({"playlistItems": playlist([("v1", "New", "text " * 100, 1)])})
        polls, shadow, counts = Y.run(pg, NOW, "r1", "k", write=False, discover_now=True, source_name="S",
                                      client=Y.Client("k", opener=ok), judge=judge_says(True), cat=CAT)
        self.assertEqual((len(polls), len(shadow), counts["videos"]), (1, 1, 1))
        self.assertIn("discovered", counts)
        self.assertNotIn("refresh", counts)  # shadow mode does not touch intelligence.items

    def test_channel_prompt_fits_the_judge_template(self):
        import judge as J
        text = Y.CHANNEL_PROMPT.format(host="youtube.com/@x", name="n", types=list(J.TYPES), lines="- t <u>")
        self.assertIn("clickbait", text)
        self.assertIn('"defence_section": null', text)
        seen = []
        j = J.Judge(providers=[("groq", "u", "k", "m")], pause=0,
                    ask=lambda p, prompt: seen.append(prompt) or '{"specialised": false, "confidence": 0.9}')
        pairs = [(f"https://www.youtube.com/watch?v={i}", f"Title {i}") for i in range(6)]
        self.assertFalse(j("youtube.com/@x", "n", pairs, prompt=Y.CHANNEL_PROMPT)["specialised"])
        self.assertIn("YouTube channel", seen[0])
        j("site.example", "n", pairs)
        self.assertNotIn("YouTube channel", seen[1])  # websites keep the old prompt

    def test_recheck_on_hold_and_old_rules_and_prune_shadow(self):
        held, old, fresh = "UC" + "h" * 22, "UC" + "o" * 22, "UC" + "f" * 22
        rows = [(Y.channel_url(held), "on_hold", "a5tc-youtube-0.1", "UA", "uk", "media", {"category": "ew"}),
                (Y.channel_url(old), "accepted", "a5tc-youtube-0.1", "PL", "pl", "media", None),
                (Y.channel_url(fresh), "accepted", Y.RULES, "DE", "de", "media", None)]
        pg = PG({"from source_discovery.source_state s where s.delivery": rows})
        api = API({"playlistItems": playlist([(f"v{i}", f"T{i}", "", i) for i in range(6)])})
        _, _, counts = Y.run(pg, NOW, "r1", "k", write=False, discover_now=True, source_name="S",
                             client=Y.Client("k", opener=api), judge=judge_says(False), cat=CAT)
        self.assertEqual(counts["rechecked"], 2)
        self.assertEqual(counts["discovered"], {"rejected": 2})
        upserts = [s for s, _ in pg.log if s.startswith("insert into source_discovery.source_state")]
        self.assertIn("is distinct from excluded.rules_version", upserts[0])
        deletes = [s for s, _ in pg.log if s.startswith("delete")]
        self.assertEqual(len(deletes), 1)
        self.assertIn("source_discovery.feed_items_shadow", deletes[0])
        self.assertIn("shadow_pruned", counts)


if __name__ == "__main__":
    unittest.main()
