"""Whole cycle on fictional data, no network (decision 5 of the 18.11 review): search -> rank -> caps -> observe
-> admit -> measure, with a specialised outlet, a general outlet, an official site without section links, a known
site and a failed query. Run in the venv or system Python."""
import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import search_cycle as C  # noqa: E402
import observer as O  # noqa: E402
from codex_discovery.radars import Response  # noqa: E402

HTML = {"content-type": "text/html; charset=utf-8"}
RSS = {"content-type": "application/rss+xml"}
NOW = dt.datetime.now(dt.timezone.utc)
DEF = ["Army orders new air defence radars", "Drone maker wins military contract", "Navy frigate programme delayed",
       "Defence ministry signs ammunition deal", "Missile production line opens", "Armoured vehicle upgrade approved"]
CIVIL = ["Football club wins the national cup final", "New bakery opens in the old town square",
         "Weather service warns of heavy rain this weekend", "Local elections set for early next spring",
         "Train timetable changes from next Monday morning", "Museum shows a new collection of modern art",
         "City council debates the new parking fees", "Popular singer announces a summer concert tour",
         "Hospital opens a new children's ward today", "School holidays start earlier this year again",
         "Bridge repairs close the main road for a week", "Farmers market returns to the central park"]


def rss(host, titles):
    stamp = (NOW - dt.timedelta(days=2)).strftime("%a, %d %b %Y 08:00:00 GMT")
    items = "".join(f"<item><title>{t}</title><link>https://{host}/a/{i}</link><pubDate>{stamp}</pubDate></item>"
                    for i, t in enumerate(titles))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>{items}</channel></rss>'.encode()


class FakeHTTP:
    def __init__(self, pages):
        self.pages, self.calls = pages, []

    def request(self, url, robots=False, **_):
        self.calls.append(url)
        status, headers, body = self.pages.get(url, (404, HTML, b""))
        return Response(status, body, url, headers)


day = NOW - dt.timedelta(days=3)
NEWS = "".join(f'<a href="/news/{day:%Y/%m/%d}/n{i}">{t} {i}</a>' for i, t in enumerate(DEF))
PAGES = {
    "https://defence-news.example.pl/": (200, HTML, b'<html lang="pl"><link rel="alternate" type="application/rss+xml" href="/rss"></html>'),
    "https://defence-news.example.pl/rss": (200, RSS, rss("defence-news.example.pl", DEF)),
    "https://generalnews.example.de/": (200, HTML, "".join(f'<a href="/s/{i}">{t}</a>' for i, t in enumerate(CIVIL)).encode()),
    "https://www.mod.example.gov/": (200, HTML, b'<html lang="en"><body>app</body></html>'),
    "https://www.mod.example.gov/news": (200, HTML, ('<html lang="en">' + NEWS + "</html>").encode()),
}


def search(job):
    if job["query"] == "broken":
        raise RuntimeError("TAVILY_CLI_FAILED")
    return {"results": {
        "q-pl": [{"url": "https://defence-news.example.pl/a/1", "title": "t"}, {"url": "https://www.defence24.com/x"},
                 {"url": "https://generalnews.example.de/s/1", "title": "t"}],
        "q-gov": [{"url": "https://www.mod.example.gov/news/1", "title": "t"}],
    }.get(job["query"], [])}


JOBS = [{"layer": "layer1", "country": "PL", "category": "ew", "lang": "pl", "type_hint": "media", "query": "q-pl",
         "topic": "news", "max_results": 10},
        {"layer": "skeleton", "country": "UA", "role": "ministry", "lang": "en", "type_hint": "agency",
         "query": "q-gov", "topic": "general", "max_results": 5},
        {"layer": "layer3", "event_id": 1, "lang": "en", "type_hint": "media", "query": "broken", "topic": "news",
         "max_results": 10}]


class Flow(unittest.TestCase):
    def test_live_cycle_imports_resolve(self):  # cycle 2 first start: import order broke main() only
        Budget, HTTP = C.network_parts()
        self.assertTrue(callable(Budget) and callable(HTTP))

    def test_whole_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = C.A.open_db(str(Path(tmp) / "r.sqlite"))
            http = FakeHTTP(PAGES)
            raw = []
            cands, found, done, trace, obs, counts, results = C.execute(
                JOBS, search, http, conn, {"defence24.com"}, raw=raw)
            by = {r["candidate_url"]: r for r in results}
            self.assertEqual(trace["failed"], 1)
            self.assertEqual(len(raw), 4)                       # every search result kept for replays
            self.assertNotIn("https://www.defence24.com/", by)  # known site never re-issued
            self.assertEqual(by["https://defence-news.example.pl/"]["decision"], "accept")
            self.assertEqual(by["https://generalnews.example.de/"]["decision"], "general")
            self.assertEqual(http.calls.count("https://generalnews.example.de/"), 1)
            self.assertFalse([c for c in http.calls if c.startswith("https://generalnews.example.de/") and c.endswith("rss")])
            gov = by["https://www.mod.example.gov/"]
            self.assertEqual((gov["decision"], gov["type"]), ("accept", "agency"))
            m = C.measure_cycle(cands, found, results, {"defence24.com"})
            self.assertEqual((m["general_outlets"], m["accepted_share"], m["disputed_for_owner"]), (1, 1.0, 0))
            self.assertEqual(m["accepted_share_incl_general"], 0.667)
            self.assertEqual(m["known_reissued"], 0)
            conn.close()

    def test_defence_section_of_a_general_outlet_is_a_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = C.A.open_db(str(Path(tmp) / "r.sqlite"))
            C.execute(JOBS, search, FakeHTTP(PAGES), conn, {"defence24.com"})  # generalnews -> general_outlet
            self.assertEqual(C.general_sites(conn)[0]["site"], "example.de")
            pages = dict(PAGES)
            pages["https://generalnews.example.de/verteidigung/"] = (200, HTML, (
                '<html lang="de"><link rel="alternate" type="application/rss+xml" href="/verteidigung/rss"></html>'
            ).encode())
            pages["https://generalnews.example.de/verteidigung/rss"] = (200, RSS, rss("generalnews.example.de", DEF))
            job = {"layer": "section", "site": "example.de", "lang": "de", "type_hint": "media",
                   "query": "Verteidigung", "include_domains": ["example.de"], "topic": "general", "max_results": 5}

            def section_search(j):
                return {"results": [{"url": "https://generalnews.example.de/verteidigung/artikel-1"},
                                    {"url": "https://generalnews.example.de/sport/x"}]}
            cands, _, _, _, _, counts, results = C.execute([job], section_search, FakeHTTP(pages), conn, set())
            self.assertEqual([c["url"] for c in cands], ["https://generalnews.example.de/verteidigung/"])
            self.assertEqual((results[0]["decision"], results[0]["reason_codes"]), ("accept", []))
            conn.close()

    def test_short_outage_does_not_stop_the_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            conn = C.A.open_db(str(Path(tmp) / "r.sqlite"))
            down = {"n": 0}

            class Flaky(FakeHTTP):
                def request(self, url, robots=False, **kw):
                    if url.startswith("https://dead") and down["n"] < 5:
                        down["n"] += 1
                        raise O.Problem("TRANSPORT_FAILURE")
                    return super().request(url, robots, **kw)
            pages = dict(PAGES)
            for i in range(5):
                pages[f"https://dead{i}.org/"] = PAGES["https://defence-news.example.pl/"]
            def srch(job):
                return {"results": [{"url": f"https://dead{i}.org/a"} for i in range(5)]}
            job = {"layer": "layer1", "country": "PL", "category": "ew", "lang": "pl", "type_hint": "media",
                   "query": "q", "topic": "news", "max_results": 10}
            real = C.network_up
            C.network_up = lambda waits=3, pause=60: True
            try:
                cands, _, _, trace, obs, counts, _ = C.execute([job], srch, Flaky(pages), conn, set())
            finally:
                C.network_up = real
            self.assertNotIn("stop", trace)
            self.assertEqual(trace.get("network_recovered"), 1)
            self.assertEqual(len(obs), 5)
            self.assertTrue(all(o["fetch"].get("status") == 200 for o in obs))
            conn.close()


if __name__ == "__main__":
    unittest.main()
