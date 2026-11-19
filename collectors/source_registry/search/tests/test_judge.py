"""Site judge with a fake model (no network): parsing, fallbacks, and its effect on the first stage and the type."""
import datetime as dt
import sys
import unittest
import urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import admission as A  # noqa: E402
import judge as J  # noqa: E402
import observer as O  # noqa: E402
from codex_discovery.radars import Response  # noqa: E402

HTML = {"content-type": "text/html; charset=utf-8"}
RSS = {"content-type": "application/rss+xml"}
NOW = dt.datetime.now(dt.timezone.utc)
CIVIL = [f"City council debates new parking fees number {i}" for i in range(12)]
DEF = ["Army orders new air defence radars", "Drone maker wins military contract", "Navy frigate programme delayed",
       "Defence ministry signs ammunition deal", "Missile production line opens", "Armoured vehicle upgrade approved"]


def page(headlines, nav=()):
    links = "".join(f'<a href="/n/{i}">{t}</a>' for i, t in enumerate(headlines))
    links += "".join(f'<a href="/{p}/">{p.title()}</a>' for p in nav)
    return f'<html lang="en">{links}</html>'.encode()


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


def judge_says(answer):
    return J.Judge(providers=[("fake", None, "k", "m")], ask=lambda p, prompt: answer, pause=0)


class Parse(unittest.TestCase):
    def test_parse_keeps_only_listed_section_and_known_type(self):
        links = {"https://n.example/defence/"}
        v = J.parse('{"specialised": false, "type": "media", "defence_section": "https://n.example/defence/", '
                    '"confidence": 0.8}', links)
        self.assertEqual(v["defence_section"], "https://n.example/defence/")
        v = J.parse('text {"specialised": true, "type": "blog", "defence_section": "https://other/", "confidence": 3}', links)
        self.assertEqual((v["type"], v["defence_section"], v["confidence"]), (None, None, 1.0))
        self.assertIsNone(J.parse("no json", links))

    def test_failing_provider_switched_off_next_answers(self):
        calls = []

        def ask(p, prompt):
            calls.append(p[0])
            if p[0] == "bad":
                raise OSError("down")
            return '{"specialised": true, "type": "media", "defence_section": null, "confidence": 0.9}'
        j = J.Judge(providers=[("bad", None, "k", "m"), ("good", None, "k", "m")], ask=ask, pause=0)
        pairs = [(f"https://s.example/{i}", f"Headline number {i} here") for i in range(6)]
        for _ in range(3):
            self.assertEqual(j("s.example", "S", pairs)["by"], "good")
        self.assertEqual(calls.count("bad"), 2)
        self.assertEqual(j.off, {"bad"})

    def test_rate_limit_waits_access_denied_switches_off_at_once(self):
        def http_error(code):
            return urllib.error.HTTPError("https://x", code, "x", {}, None)
        answers = {"limited": [http_error(429)] * 3 + [None], "denied": [http_error(403)]}
        calls = []

        def ask(p, prompt):
            calls.append(p[0])
            seq = answers.get(p[0])
            exc = seq.pop(0) if seq else None
            if exc:
                raise exc
            return '{"specialised": true, "type": "media", "defence_section": null, "confidence": 0.9}'
        j = J.Judge(providers=[("denied", None, "k", "m"), ("limited", None, "k", "m"), ("good", None, "k", "m")],
                    ask=ask, pause=0)
        pairs = [(f"https://s.example/{i}", f"Headline number {i} here") for i in range(6)]
        by = [j("s.example", "S", pairs)["by"] for _ in range(4)]
        self.assertEqual(by, ["good", "good", "good", "limited"])
        self.assertEqual(calls.count("denied"), 1)
        self.assertEqual(j.off, {"denied"})

    def test_named_user_agent_is_sent(self):
        seen = {}

        def fake_urlopen(req, timeout=0):
            seen.update(req.header_items())
            raise urllib.error.URLError("offline")
        real, J.urllib.request.urlopen = J.urllib.request.urlopen, fake_urlopen
        try:
            with self.assertRaises(urllib.error.URLError):
                J._ask(("groq", "https://api.groq.com/openai/v1/chat/completions", "k", "m"), "p")
        finally:
            J.urllib.request.urlopen = real
        self.assertTrue(seen.get("User-agent", "").startswith("bohun-source-search/"))


class FirstStage(unittest.TestCase):
    def tearDown(self):
        O.JUDGE = None

    def observe(self, answer, pages):
        O.JUDGE = judge_says(answer)
        return O.observe({"url": "https://news.example.org/", "prefilter": True,
                          "found_via": {"layer": "layer1"}}, FakeHTTP(pages))

    def test_general_site_with_defence_section_is_checked_there(self):
        pages = {"https://news.example.org/": (200, HTML, page(CIVIL, nav=("defence", "sport"))),
                 "https://news.example.org/defence/": (200, HTML, (
                     '<html lang="en"><link rel="alternate" type="application/rss+xml" href="/defence/rss"></html>').encode()),
                 "https://news.example.org/defence/rss": (200, RSS, rss("news.example.org", DEF))}
        o = self.observe('{"specialised": false, "type": "media", "defence_section": '
                         '"https://news.example.org/defence/", "confidence": 0.9}', pages)
        self.assertEqual((o["delivery"], o["judged_section"]), ("feed", "https://news.example.org/defence/"))
        r = A.decide(o, lambda c, h: (None, None))
        self.assertEqual(r["decision"], "accept")

    def test_general_site_without_section_is_general_and_typed_by_judge(self):
        pages = {"https://news.example.org/": (200, HTML, page(CIVIL))}
        o = self.observe('{"specialised": false, "type": "company_news", "defence_section": null, "confidence": 0.7}',
                         pages)
        self.assertIn("general_outlet", o)
        r = A.decide(o, lambda c, h: (None, None))
        self.assertEqual((r["decision"], r["type"], r["type_basis"]), ("general", "company_news", "judge:fake"))

    def test_specialised_gets_full_check_even_with_few_defence_words(self):
        pages = {"https://news.example.org/": (200, HTML, page(CIVIL))}
        o = self.observe('{"specialised": true, "type": "media", "defence_section": null, "confidence": 0.8}', pages)
        self.assertNotIn("general_outlet", o)
        self.assertEqual(o["judge"]["specialised"], True)


if __name__ == "__main__":
    unittest.main()
