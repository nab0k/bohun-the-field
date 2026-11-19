"""Observer tests on fictional sites (no network). Run: /opt/homebrew/bin/python3.13 test_observer.py"""
import datetime as dt
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import observer as O  # noqa: E402
import admission as A  # noqa: E402
from codex_discovery.radars import Response  # noqa: E402

NOW = dt.datetime(2026, 11, 18, 12, 0, tzinfo=dt.timezone.utc)
HTML = {"content-type": "text/html; charset=utf-8"}
RSS = {"content-type": "application/rss+xml"}


def rss(titles):
    items = "".join(f"<item><title>{t}</title><link>https://firm.example.de/news/{i}</link>"
                    f"<pubDate>Mon, {10 + i} Nov 2026 08:00:00 GMT</pubDate></item>" for i, t in enumerate(titles))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Firm</title>{items}</channel></rss>'.encode()


class FakeHTTP:
    def __init__(self, pages, errors=None):
        self.pages, self.errors, self.calls = pages, errors or {}, []

    def request(self, url, robots=False, **_):
        self.calls.append(url)
        if url in self.errors:
            raise O.Problem(self.errors[url])
        status, headers, body, final = self.pages.get(url, (404, HTML, b"", url))
        return Response(status, body, final, headers)


TITLES = ["Firma liefert Drohnen an die Bundeswehr", "Neuer Auftrag für Munition", "Messe: Rüstung und Verteidigung",
          "Quartalszahlen veröffentlicht", "Neues Werk für Radarsysteme"]


class Observe(unittest.TestCase):
    def test_home_links_feed_in_german_section(self):
        home = (b'<html lang="de"><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head>'
                b'<body><a href="/aktuelles">Aktuelles</a></body></html>')
        http = FakeHTTP({"https://firm.example.de/": (200, HTML, home, "https://firm.example.de/"),
                         "https://firm.example.de/feed.xml": (200, RSS, rss(TITLES), "https://firm.example.de/feed.xml")})
        o = O.observe({"url": "https://firm.example.de/", "guessed_type": "company_news", "guessed_country": "DE"}, http)
        self.assertEqual((o["delivery"], o["delivery_url"], len(o["items"])), ("feed", "https://firm.example.de/feed.xml", 5))
        self.assertEqual(o["html_lang"], "de")
        r = A.decide(o, lambda c, h: (None, None), NOW)
        self.assertEqual((r["decision"], r["country"], r["type"]), ("accept", "DE", "company_news"))

    def test_section_words_beyond_polish(self):
        body = b'<a href="/fr/actualites">Actualit\xc3\xa9s</a><a href="/about">About</a><a href="/sv/nyheter">x</a>'
        links = O.section_links("https://x.example.fr/", body)
        self.assertEqual([u for u, _ in links], ["https://x.example.fr/fr/actualites", "https://x.example.fr/sv/nyheter"])

    def test_cyrillic_and_label_first(self):
        body = ('<a href="/bg/products/news-gadget">Gadget</a><a href="/bg/novini">Новини</a>'
                '<a href="/stories">Discover more Stories</a>').encode()
        links = [u for u, _ in O.section_links("https://mod.example.bg/", body)]
        self.assertEqual(links[:2], ["https://mod.example.bg/bg/novini", "https://mod.example.bg/stories"])
        self.assertIn("https://mod.example.bg/bg/products/news-gadget", links)

    def test_errors_map_to_checks(self):
        for code, want in (("ROBOTS_DISALLOWED", "robots_denied"), ("TRANSPORT_FAILURE", "transient_error"),
                           ("DNS_UNAVAILABLE", "transient_error"),
                           ("ROBOTS_UNAVAILABLE", "robots_unreadable"), ("REDIRECT_HOLD", "unreachable")):
            o = O.observe({"url": "https://a.example.org/"}, FakeHTTP({}, {"https://a.example.org/": code}))
            self.assertEqual(A.decide(o, lambda c, h: (None, None), NOW)["reason_codes"], [want], code)

    def test_cap_stops_the_cycle(self):
        with self.assertRaises(O.Problem):
            O.observe({"url": "https://a.example.org/"}, FakeHTTP({}, {"https://a.example.org/": "HTTP_REQUEST_CAP"}))

    def test_home_without_sections_is_disputed(self):
        http = FakeHTTP({"https://b.example.org/": (200, HTML, b"<html><a href='/about'>About</a></html>", "https://b.example.org/")})
        o = O.observe({"url": "https://b.example.org/"}, http)
        self.assertEqual(A.decide(o, lambda c, h: (None, None), NOW)["reason_codes"], ["no_news_site"])
        self.assertEqual(http.calls, ["https://b.example.org/"] + ["https://b.example.org" + g for g in O.SECTION_GUESSES + O.FEED_GUESSES])

    def test_feed_found_by_guess(self):
        http = FakeHTTP({"https://d.example.de/": (200, HTML, b"<html><a href='/about'>About</a></html>", "https://d.example.de/"),
                         "https://d.example.de/rss.xml": (200, RSS, rss(TITLES), "https://d.example.de/rss.xml")})
        o = O.observe({"url": "https://d.example.de/"}, http)
        self.assertEqual((o["delivery"], o["delivery_url"]), ("feed", "https://d.example.de/rss.xml"))

    def test_undated_section_goes_to_check7(self):
        links = "".join(f'<a href="/news/story-{i}">Company signs new supply contract number {i}</a>' for i in range(6))
        http = FakeHTTP({"https://e.example.org/": (200, HTML, b'<a href="/news">News</a>', "https://e.example.org/"),
                         "https://e.example.org/news": (200, HTML, links.encode(), "https://e.example.org/news")})
        o = O.observe({"url": "https://e.example.org/"}, http)
        self.assertEqual((o["delivery"], len(o["items"])), ("page", 6))
        self.assertEqual(A.decide(o, lambda c, h: (None, None), NOW)["reason_codes"], ["no_dates"])

    def test_http_only_is_disputed(self):
        o = O.observe({"url": "https://f.example.org/"}, FakeHTTP({}, {"https://f.example.org/": "URL_HTTPS_REQUIRED"}))
        self.assertEqual(A.decide(o, lambda c, h: (None, None), NOW)["reason_codes"], ["http_only"])

    def test_at_most_two_targets(self):
        links = "".join(f'<a href="/news/{i}">News {i}</a>' for i in range(5)).encode()
        http = FakeHTTP({"https://c.example.org/": (200, HTML, links, "https://c.example.org/")})
        O.observe({"url": "https://c.example.org/"}, http)
        self.assertEqual(len(http.calls), 1 + 3 + len(O.FEED_GUESSES))  # home, three sections, feed guesses


class Gzip(unittest.TestCase):
    def test_decode(self):
        import gzip
        import zlib
        import transport_ext as T
        body = b"<html>" + b"x" * 1000 + b"</html>"
        self.assertEqual(T.decode(gzip.compress(body), "gzip"), body)
        self.assertEqual(T.decode(zlib.compress(body), "deflate"), body)
        self.assertEqual(T.decode(body, None), body)
        with self.assertRaises(O.Problem):
            T.decode(body, "br")
        with self.assertRaises(O.Problem):
            T.decode(gzip.compress(b"0" * (T.MAX_DECODED + 10)), "gzip")


class Dates20261118(unittest.TestCase):
    """Cycle 1 fixes: dates in link text/address, Atom updated, section guesses when the home links none."""

    def test_date_in_text_and_path(self):
        self.assertEqual(O.date_in("17 November 2026 MANGROVE advances")[:10], "2026-11-17")
        self.assertEqual(O.date_in("Опубліковано 17 листопада 2026")[:10], "2026-11-17")
        self.assertEqual(O.date_in("Meldung vom 3. März 2026")[:10], "2026-03-03")
        self.assertEqual(O.date_in("17.11.2026 Pressemitteilung")[:10], "2026-11-17")
        self.assertEqual(O.date_in("x", "/news/2026/11/04/item")[:10], "2026-11-04")
        self.assertIsNone(O.date_in("05/11/2026 ambiguous order"))
        self.assertIsNone(O.date_in("Weitere Aufträge der Bundeswehr", "/de/aktuelles"))

    @unittest.skipIf(O._ready is None, "feedparser only in the venv")
    def test_atom_updated_when_no_published(self):
        atom = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><title>T</title>'
                '<entry><title>Neue Drohnen</title><link href="https://t.example.de/a"/>'
                '<updated>2026-11-17T08:00:00Z</updated></entry></feed>').encode()
        items = O._ready.parse_feed(atom, "https://t.example.de/feed")
        self.assertEqual((items[0]["date"][:10], items[0]["date_basis"]), ("2026-11-17", "updated"))

    def test_section_guess_when_home_links_no_section(self):
        links = "".join(f'<a href="/news/2026/11/{d:02d}/item-{d}">Ministry signs drone contract number {d}</a>'
                        for d in range(10, 16))
        http = FakeHTTP({"https://mod.example.ua/": (200, HTML, b"<html><body>app</body></html>", "https://mod.example.ua/"),
                         "https://mod.example.ua/news": (200, HTML, links.encode(), "https://mod.example.ua/news")})
        o = O.observe({"url": "https://mod.example.ua/"}, http)
        self.assertEqual((o["delivery"], o["delivery_url"]), ("page", "https://mod.example.ua/news"))
        self.assertTrue(all(i["date"] for i in o["items"]))


if __name__ == "__main__":
    unittest.main()
