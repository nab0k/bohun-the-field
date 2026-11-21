"""A4SC-05 feed collector without network or database: eligibility, canonical ids, fresh entries, the content
contract, robots and conditional GET, one failing feed, shadow writes with the duplicate check, counts-only output."""
import datetime as dt
import io
import json
import unittest
import urllib.error

from collectors.feeds import collect as F

NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)


def rss(items):
    body = "".join(f"<item><title>{t}</title><link>{l}</link><pubDate>{d}</pubDate>"
                   f"<description><![CDATA[<p>{x}</p>]]></description></item>" for t, l, d, x in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>{body}</channel></rss>'.encode()


FRESH = "Thu, 19 Nov 2026 08:00:00 GMT"
OLD = "Mon, 02 Nov 2026 08:00:00 GMT"


class Resp(io.BytesIO):
    def __init__(self, status, body=b"", headers=None):
        super().__init__(body)
        self.status, self.headers = status, headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Net:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def __call__(self, req, timeout):
        self.calls.append((req.full_url, dict(req.header_items())))
        status, body, headers = self.pages.get(req.full_url, (404, b"", {}))
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "x", headers, None)
        return Resp(status, body, headers)


class Collect(unittest.TestCase):
    def test_eligible_and_canonical(self):
        rows = [("c1", "https://a.example/feed", "a2sa-shadow-0.7", True, [], None, None),
                ("c2", "https://b.example/feed", "a2sa-shadow-0.5", False, [], None, None),
                ("c3", "https://c.example/feed", "a2sa-shadow-0.6", False, ["owner_accept"], "E", None)]
        self.assertEqual([f["channel_id"] for f in F.eligible(rows, 10)], ["c1", "c3"])
        self.assertEqual(F.canonical("https://WWW.A.example/x?utm_source=rss&id=7#top"), "https://www.a.example/x?id=7")
        self.assertEqual(F.external_id("https://a.example/x?utm_medium=y"), F.external_id("https://a.example/x"))

    def test_entries_fresh_dated_with_contract(self):
        body = rss([("New", "https://a.example/n?utm_source=rss", FRESH, "Army orders radars. " * 40),
                    ("Old", "https://a.example/o", OLD, "old"), ])
        got = F.entries(body, "https://a.example/feed", NOW)
        self.assertEqual([(e["title"], e["url"]) for e in got], [("New", "https://a.example/n")])
        self.assertEqual(F.entries.total, 2)
        cap = F.capture(got[0]["html"], NOW)
        self.assertEqual(cap["provider_field"], "summary.content")
        self.assertGreater(cap["text_characters"], 400)

    def test_poll_robots_conditional_and_failures(self):
        pages = {"https://a.example/robots.txt": (200, b"User-agent: *\nDisallow: /private\n", {}),
                 "https://a.example/feed": (200, rss([("New", "https://a.example/n", FRESH, "x " * 300)]),
                                            {"ETag": '"v1"'}),
                 "https://a.example/private/feed": (200, b"never", {}),
                 "https://b.example/robots.txt": (404, b"", {}),
                 "https://b.example/feed": (304, b"", {}),
                 "https://c.example/robots.txt": (404, b"", {}),
                 "https://c.example/feed": (500, b"", {})}
        net = Net(pages)
        fetcher = F.Fetcher(opener=net, sleep=lambda s: None)
        feeds = [{"channel_id": "a", "url": "https://a.example/feed"},
                 {"channel_id": "p", "url": "https://a.example/private/feed"},
                 {"channel_id": "b", "url": "https://b.example/feed", "etag": '"old"'},
                 {"channel_id": "c", "url": "https://c.example/feed"}]
        polls, shadow = F.poll(feeds, fetcher, NOW)
        by = {p["channel_id"]: p for p in polls}
        self.assertEqual((by["a"]["http_status"], by["a"]["etag"], by["a"]["fresh"]), (200, '"v1"', 1))
        self.assertEqual(by["p"]["error"], "robots")
        self.assertEqual((by["b"]["http_status"], by["b"]["error"]), (304, None))
        self.assertEqual(by["c"]["error"], "http_500")
        self.assertNotIn("https://a.example/private/feed", [u for u, _ in net.calls])
        sent = dict(net.calls)["https://b.example/feed"]
        self.assertEqual(sent.get("If-none-match"), '"old"')
        self.assertTrue(all(h.get("User-agent", "").startswith("BOHUN-FeedCollector/") for _, h in net.calls))
        self.assertEqual(len(net.calls), 6)  # robots once per host (3) + one GET per allowed feed (3)
        self.assertEqual(len(shadow), 1)

    def test_save_shadow_marks_known_urls_and_writes_only_shadow(self):
        class Cur:
            def __init__(self, pg):
                self.pg, self.rowcount, self.rows = pg, 1, []

            def execute(self, sql, params=None):
                self.pg.sql.append(" ".join(sql.split())[:60])
                self.rows = [("https://a.example/n/",)] if sql.startswith("select url") else []

            def fetchall(self):
                return self.rows

        class PG:
            def __init__(self):
                self.sql = []

            def cursor(self):
                return Cur(self)

            def transaction(self):
                import contextlib
                return contextlib.nullcontext()
        pg = PG()
        shadow = [{"channel_id": "a", "external_id": "e1", "url": "https://a.example/n", "title": "t",
                   "published_at": NOW, "text_chars": 500, "capture_status": "BODY_PRESENT_COMPLETENESS_UNVERIFIED"}]
        polls = [{"channel_id": "a", "http_status": 200, "etag": None, "last_modified": None, "entries": 1,
                  "fresh": 1, "error": None}]
        saved = F.save_shadow(pg, "r1", polls, shadow)
        known = saved.pop("_known")
        self.assertEqual(saved, {"new_shadow_items": 1, "already_in_items": 0})
        self.assertEqual(known, {"https://a.example/n/"})
        writes = [s for s in pg.sql if s.startswith("insert")]
        self.assertTrue(all("source_discovery." in s for s in writes))
        self.assertFalse(any("intelligence.items" in s and s.startswith("insert") for s in pg.sql))
        out = json.dumps(F.summary(polls, shadow, saved, F.Fetcher(opener=None)))
        self.assertNotIn("a.example", out)  # public log: counts only


    def test_write_items_one_source_skips_known_and_keeps_contract(self):
        import sys
        import types
        fake_json = types.ModuleType("psycopg.types.json")
        fake_json.Jsonb = lambda v: v
        sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))
        sys.modules.setdefault("psycopg.types", types.ModuleType("psycopg.types"))
        sys.modules["psycopg.types.json"] = fake_json
        log = []

        class Cur:
            def execute(self, sql, params=None):
                log.append((" ".join(sql.split()), params))
                self.row = (7,) if "select id from intelligence.sources" in sql else (len(log),)

            def fetchone(self):
                return self.row

        class PG:
            def cursor(self):
                return Cur()

            def transaction(self):
                import contextlib
                return contextlib.nullcontext()
        cap = F.capture("<p>" + "Army orders radars. " * 40 + "</p>", NOW)
        shadow = [{"channel_id": "a", "external_id": "e1", "url": "https://a.example/n", "title": "New",
                   "published_at": NOW, "feed_url": "https://a.example/feed", "origin_title": "A", "capture": cap},
                  {"channel_id": "a", "external_id": "e2", "url": "https://a.example/dup", "title": "Dup",
                   "published_at": NOW, "feed_url": "https://a.example/feed", "origin_title": "A", "capture": cap}]
        out = F.write_items(PG(), "r1", shadow, known={"https://a.example/dup"})
        self.assertEqual(out, {"items_inserted": 1})
        inserts = [p for s, p in log if s.startswith("insert into intelligence.items")]
        self.assertEqual(len(inserts), 1)
        source_id, eid, title, url, published, raw = inserts[0]
        self.assertEqual((source_id, eid, url), (7, "e1", "https://a.example/n"))
        self.assertEqual(raw["collector"], "a4sc-feeds-v1")
        self.assertEqual(raw["content_capture"]["provider_field"], "summary.content")
        self.assertTrue(all("on conflict (source_id, external_id) do nothing" in s
                            for s, _ in log if s.startswith("insert into intelligence.items")))

    def test_inoreader_compare_feeds_are_shadow_only(self):
        class Cur:
            def execute(self, sql, params=None):
                self.sql = " ".join(sql.split())

            def fetchall(self):
                return [("ib_1", "https://a.example/feed", None, None), ("ib_2", "not-a-url", None, None)]
        pages = {"https://a.example/robots.txt": (404, b"", {}),
                 "https://a.example/feed": (200, rss([("New", "https://a.example/n", FRESH, "x " * 300)]), {})}
        cur = Cur()
        polls, shadow = F.compare_feeds(cur, F.Fetcher(opener=Net(pages), sleep=lambda s: None), NOW)
        self.assertIn("s.delivery = 'feed_compare'", cur.sql)
        self.assertEqual([p["channel_id"] for p in polls], ["ib_1"])
        self.assertEqual(len(shadow), 1)
        self.assertTrue(shadow[0]["skip_write"])

    def test_relay_only_for_403_and_network_errors_never_429(self):
        relay = ("https://relay.example", "tok")
        rel = "https://relay.example/fetch?url="
        pages = {"https://b.example/robots.txt": (404, b"", {}),
                 "https://b.example/feed": (403, b"", {}),
                 rel + "https%3A%2F%2Fb.example%2Ffeed": (200, rss([("New", "https://b.example/n", FRESH, "x " * 300)]),
                                                          {"X-Relay": "1"}),
                 "https://c.example/robots.txt": (404, b"", {}),
                 "https://c.example/feed": (429, b"", {})}
        net = Net(pages)
        f = F.Fetcher(opener=net, sleep=lambda s: None, relay=relay)
        polls, shadow = F.poll([{"channel_id": "b", "url": "https://b.example/feed"},
                                {"channel_id": "c", "url": "https://c.example/feed"}], f, NOW)
        by = {p["channel_id"]: p for p in polls}
        self.assertEqual((by["b"]["http_status"], by["b"]["fresh"]), (200, 1))
        self.assertEqual(by["c"]["error"], "http_429")
        self.assertFalse(any(u.startswith(rel + "https%3A%2F%2Fc.example") for u, _ in net.calls))
        self.assertEqual(dict(f.relayed), {"http_403": 1})
        sent = dict(net.calls)[rel + "https%3A%2F%2Fb.example%2Ffeed"]
        self.assertEqual(sent.get("X-relay-token"), "tok")

        def down(req, timeout):
            if req.full_url.startswith(rel):
                return net(req, timeout)
            raise urllib.error.URLError("reset")
        g = F.Fetcher(opener=down, sleep=lambda s: None, relay=relay)
        self.assertEqual(g._get("https://b.example/feed")[0], 200)
        self.assertEqual(dict(g.relayed), {"URLError": 1})
        # the Worker refused (no X-Relay mark): the direct answer stands; no relay configured: errors as before
        net.pages[rel + "https%3A%2F%2Fb.example%2Ffeed"] = (401, b"", {})
        self.assertEqual(F.Fetcher(opener=net, sleep=lambda s: None, relay=relay)._get("https://b.example/feed")[0], 403)
        with self.assertRaises(urllib.error.URLError):
            F.Fetcher(opener=down, sleep=lambda s: None, relay=())._get("https://b.example/feed")

    def test_whole_feed_new_reads_the_older_page(self):
        def wp(items, paged=False):
            body = rss(items)
            return body.replace(b"<title>T</title>", b"<title>T</title><generator>https://wordpress.org/?v=6.8</generator>")
        first = [(f"N{i}", f"https://w.example/n{i}", FRESH, "x " * 50) for i in range(10)]
        older = [(f"O{i}", f"https://w.example/o{i}", FRESH, "x " * 50) for i in range(4)] + first[-1:]
        pages = {"https://w.example/robots.txt": (404, b"", {}),
                 "https://w.example/feed": (200, wp(first), {}),
                 "https://w.example/feed?paged=2": (200, wp(older), {})}
        polls, shadow = F.poll([{"channel_id": "w", "url": "https://w.example/feed"}],
                               F.Fetcher(opener=Net(pages), sleep=lambda s: None, relay=()), NOW)
        self.assertEqual((polls[0]["fresh"], polls[0]["paged"]), (14, 4))
        self.assertEqual(len({s["url"] for s in shadow}), 14)
        few = {"https://w.example/robots.txt": (404, b"", {}), "https://w.example/feed": (200, wp(first[:5]), {})}
        net = Net(few)
        F.poll([{"channel_id": "w", "url": "https://w.example/feed"}], F.Fetcher(opener=net, sleep=lambda s: None,
                                                                                 relay=()), NOW)
        self.assertNotIn("https://w.example/feed?paged=2", [u for u, _ in net.calls])

    def test_busy_sql_thresholds(self):
        self.assertIn("having count(*) >= %s", " ".join(F.BUSY_SQL.split()))
        self.assertIn("fresh >= entries", F.BUSY_SQL)
        self.assertEqual(F.BUSY_PER_WEEK, 56)


if __name__ == "__main__":
    unittest.main()
