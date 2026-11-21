"""A4SC-03 pages without a feed, no network: headline-link groups (the Web Monitor cases), topic words, site maps,
the hidden feed, the first-poll baseline, article reading with the 300-character gate, budgets and the write switch."""
import datetime as dt
import unittest

from collectors.feeds import collect as F
from collectors.feeds import pages as P
from tests.feeds.test_collect import NOW, Net, rss

TEXT = "Ministry signs a contract for new air defence radars with a national company. " * 8


def html(body, head=""):
    return f"<html><head>{head}</head><body>{body}</body></html>".encode()


def cards(paths, prefix="https://www.gov.example"):
    return "".join(f'<div class="art-prev"><a href="{prefix}{p}">Minister signs a new radar contract number {i}</a>'
                   f"</div>" for i, p in enumerate(paths))


NAV = ('<nav><a href="/web/mod/about-the-ministry-and-its-many-departments">About the ministry and its many '
       'departments</a></nav><footer><a href="/web/mod/contact-us-for-more-information-today">Contact us for more '
       'information today</a></footer><a href="/web/mod/x.pdf">Annual report of the ministry for the year</a>')


def extract_ok(body, url):
    return {"text": TEXT, "date": "2026-11-20", "title": "Radar contract"}


class Pages(unittest.TestCase):
    def test_headline_links_poland_shape_and_chrome(self):
        """Articles beside the listing (/web/mod/<slug>, no /news/, no date) are found; nav, footer, files are not."""
        listing = "https://www.gov.example/web/mod/aktualnosci5"
        links, _ = P.parse_page(html(NAV + cards([f"/web/mod/minister-signs-{i}" for i in range(5)])), listing)
        got = P.headline_links(links, listing)
        self.assertEqual(len(got), 5)
        self.assertTrue(all("/web/mod/minister-signs-" in u for u, _ in got))
        self.assertFalse(any("about" in u or "contact" in u or u.endswith(".pdf") for u, _ in got))

    def test_largest_group_wins_and_few_links_fail(self):
        listing = "https://site.example/news"
        body = cards([f"/news/2026/11/story-{i}" for i in range(4)], "https://site.example") + \
            cards([f"/topics/t{i}/x" for i in range(2)], "https://site.example")
        got = P.headline_links(P.parse_page(html(body), listing)[0], listing)
        self.assertEqual({P.template(u) for u, _ in got}, {"/news/#/#/"})
        two = cards(["/a/one", "/a/two"], "https://site.example")
        self.assertEqual(P.headline_links(P.parse_page(html(two), listing)[0], listing), [])

    def test_topic_words(self):
        self.assertEqual(P.topic_words("https://ainonline.com/section/defense"), {"defense"})
        self.assertEqual(P.topic_words("https://defensie.nl/nieuws"), set())
        self.assertEqual(P.topic_words("https://thedefender.media/uk/categories/investments"), {"investments"})
        self.assertTrue(P.on_topic("https://ainonline.com/aviation-news/defense/2026-11-20/x", {"defense"}))
        self.assertFalse(P.on_topic("https://ainonline.com/aviation-news/business/x", {"defense"}))

    def test_sitemap_index_news_first_fresh_and_on_topic(self):
        index = ('<?xml version="1.0"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                 '<sitemap><loc>https://ain.example/pages.xml</loc><lastmod>2026-11-20</lastmod></sitemap>'
                 '<sitemap><loc>https://ain.example/news.xml</loc><lastmod>2026-11-19</lastmod></sitemap>'
                 '<sitemap><loc>https://ain.example/old-posts.xml</loc><lastmod>2025-01-01</lastmod></sitemap>'
                 '</sitemapindex>').encode()
        news = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
                'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
                '<url><loc>https://ain.example/defense/new-radar</loc><news:news><news:publication_date>'
                '2026-11-20T08:00:00Z</news:publication_date></news:news></url>'
                '<url><loc>https://ain.example/business/airline</loc><lastmod>2026-11-20</lastmod></url>'
                '<url><loc>https://ain.example/defense/old</loc><lastmod>2026-10-01</lastmod></url>'
                '<url><loc>https://other.example/defense/x</loc><lastmod>2026-11-20</lastmod></url></urlset>').encode()
        net = Net({"https://ain.example/robots.txt": (200, b"User-agent: *\nAllow: /\n"
                                                      b"Sitemap: https://ain.example/index.xml\n", {}),
                   "https://ain.example/index.xml": (200, index, {}),
                   "https://ain.example/news.xml": (200, news, {})})
        fetcher = F.Fetcher(opener=net, sleep=lambda s: None)
        got = P.from_sitemap(fetcher, "https://ain.example/section/defense", NOW)
        self.assertEqual([u for u, _ in got], ["https://ain.example/defense/new-radar"])
        asked = [u for u, _ in net.calls]
        self.assertLess(asked.index("https://ain.example/news.xml"), len(asked))
        self.assertNotIn("https://ain.example/old-posts.xml", asked)

    def test_first_poll_is_baseline_then_new_links_are_read(self):
        listing = "https://www.gov.example/web/mod/news"
        page1 = html(cards([f"/web/mod/story-{i}" for i in range(4)]))
        page2 = html(cards([f"/web/mod/story-{i}" for i in range(5)] + ["/web/mod/menu-like-link"]))
        robots = (404, b"", {})
        net = Net({"https://www.gov.example/robots.txt": robots, listing: (200, page1, {})})
        fetcher = F.Fetcher(opener=net, sleep=lambda s: None)
        page = {"channel_id": "p1", "url": listing}
        row, rows = P.poll_page(page, fetcher, NOW, set(), P.Budget(), extract_ok, write=True)
        self.assertEqual((row["method"], row.get("baseline"), len(rows)), ("links", True, 4))
        self.assertTrue(all(r["skip_write"] and r["capture_status"] == "BASELINE" for r in rows))
        seen = {r["external_id"] for r in rows}
        net.pages[listing] = (200, page2, {})
        net.pages["https://www.gov.example/web/mod/story-4"] = (200, b"<html>article</html>", {"Content-Type": "text/html"})
        net.pages["https://www.gov.example/web/mod/menu-like-link"] = (200, b"<html>x</html>", {"Content-Type": "text/html"})

        def extract(body, url):
            return extract_ok(body, url) if "story-4" in url else {"text": "short", "date": None, "title": None}
        row, rows = P.poll_page(page, F.Fetcher(opener=net, sleep=lambda s: None), NOW, seen, P.Budget(), extract,
                                write=True)
        by = {r["url"]: r for r in rows}
        new = by["https://www.gov.example/web/mod/story-4"]
        self.assertFalse(new["skip_write"])
        self.assertGreater(new["text_chars"], P.MIN_TEXT)
        self.assertEqual(new["published_at"], dt.datetime(2026, 11, 20, tzinfo=dt.timezone.utc))
        self.assertEqual(new["raw_extra"]["page_method"], "links")
        self.assertEqual(by["https://www.gov.example/web/mod/menu-like-link"]["capture_status"], "NOT_ARTICLE")
        self.assertTrue(by["https://www.gov.example/web/mod/menu-like-link"]["skip_write"])
        self.assertEqual(row["fresh"], 1)

    def test_hidden_feed_is_used_first(self):
        listing = "https://co.example/newsroom"
        head = '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        feed = rss([("Contract", "https://co.example/newsroom/contract", "Thu, 19 Nov 2026 08:00:00 GMT", TEXT)])
        net = Net({"https://co.example/robots.txt": (404, b"", {}), listing: (200, html("", head), {}),
                   "https://co.example/feed.xml": (200, feed, {})})
        row, rows = P.poll_page({"channel_id": "c", "url": listing}, F.Fetcher(opener=net, sleep=lambda s: None),
                                NOW, set(), P.Budget(), extract_ok, write=False)
        self.assertEqual((row["method"], len(rows)), ("feed", 1))
        self.assertTrue(rows[0]["skip_write"])  # write switch off: shadow only
        self.assertGreater(rows[0]["text_chars"], 300)

    def test_script_page_old_article_and_budget(self):
        listing = "https://js.example/news"
        net = Net({"https://js.example/robots.txt": (404, b"", {}), listing: (200, html("<div id=app></div>"), {})})
        row, rows = P.poll_page({"channel_id": "j", "url": listing}, F.Fetcher(opener=net, sleep=lambda s: None),
                                NOW, set(), P.Budget(), extract_ok)
        self.assertEqual((row["error"], rows), ("no_links", []))
        site = "https://www.gov.example"
        listing = site + "/web/mod/news"
        pages = {f"{site}/robots.txt": (404, b"", {}),
                 listing: (200, html(cards([f"/web/mod/story-{i}" for i in range(8)])), {})}
        for i in range(8):
            pages[f"{site}/web/mod/story-{i}"] = (200, b"<html>a</html>", {"Content-Type": "text/html"})
        net = Net(pages)
        budget = P.Budget(total=2)
        old = lambda b, u: {"text": TEXT, "date": "2026-09-01", "title": "Old"}  # noqa: E731
        row, rows = P.poll_page({"channel_id": "g", "url": listing}, F.Fetcher(opener=net, sleep=lambda s: None),
                                NOW, {"x"}, budget, old, write=True)
        self.assertEqual(len(rows), 2)  # run budget, below the per-source limit of 3
        self.assertTrue(all(r["capture_status"] == "OLD" and r["skip_write"] for r in rows))
        self.assertEqual(budget.left, 0)

    def test_write_items_skips_flagged_rows(self):
        import sys
        import types
        fake = types.ModuleType("psycopg.types.json")
        fake.Jsonb = lambda v: v
        sys.modules.setdefault("psycopg", types.ModuleType("psycopg"))
        sys.modules.setdefault("psycopg.types", types.ModuleType("psycopg.types"))
        sys.modules["psycopg.types.json"] = fake
        log = []

        class Cur:
            def execute(self, sql, params=None):
                log.append((" ".join(sql.split()), params))

            def fetchone(self):
                return (1,)

        class PG:
            def cursor(self):
                return Cur()

            def transaction(self):
                import contextlib
                return contextlib.nullcontext()
        base = {"channel_id": "p", "title": "t", "published_at": NOW, "capture": None}
        rows = [{**base, "external_id": "a", "url": "https://x.example/a", "skip_write": True},
                {**base, "external_id": "b", "url": "https://x.example/b", "skip_write": False,
                 "raw_extra": {"collector": P.COLLECTOR, "page_method": "links"}}]
        self.assertEqual(F.write_items(PG(), "r", rows, known=set()), {"items_inserted": 1})
        raw = [p for s, p in log if s.startswith("insert into intelligence.items")][0][-1]
        self.assertEqual((raw["collector"], raw["page_method"]), (P.COLLECTOR, "links"))


if __name__ == "__main__":
    unittest.main()
