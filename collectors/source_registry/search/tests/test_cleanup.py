"""Rules 0.7 (review of the 95 accepted, 19.11): bad deliveries, judged sections stay narrow, type and country fixes,
and the audit of accepted sources. No network."""
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import admission as A  # noqa: E402
import observer as O  # noqa: E402
from test_judge import DEF, HTML, RSS, FakeHTTP, judge_says, page, rss  # noqa: E402


class Delivery(unittest.TestCase):
    def test_bad_deliveries(self):
        for u in ("https://dutchdefencepress.com/comments/feed", "https://f.example/app.php/feed?sid=14f2",
                  "https://meta-defense.fr/classification/annonces-partenaires/x", "https://t.example/uk/categories/events"):
            self.assertTrue(O.bad_delivery(u), u)
        for u in ("https://dutchdefencepress.com/feed", "https://x.example/news/feed", "https://x.example/eventsfeed"):
            self.assertFalse(O.bad_delivery(u), u)

    def tearDown(self):
        O.JUDGE = None

    def test_judged_section_never_delivers_the_whole_site(self):
        civil = [f"Airline announces new route number {i} today" for i in range(12)]
        sec = '<html lang="en"><link rel="alternate" type="application/rss+xml" href="/feed">' + \
              "".join(f'<a href="/news/defense/{i}">{t}</a>' for i, t in enumerate(DEF)) + "</html>"
        pages = {"https://aero.example.com/": (200, HTML, page(civil, nav=("news/defense",))),
                 "https://aero.example.com/news/defense/": (200, HTML, sec.encode()),
                 "https://aero.example.com/feed": (200, RSS, rss("aero.example.com", civil[:4] + DEF[:2])),
                 "https://aero.example.com/feed/": (200, RSS, rss("aero.example.com", civil[:4] + DEF[:2]))}
        O.JUDGE = judge_says('{"specialised": false, "type": "media", "defence_section": '
                             '"https://aero.example.com/news/defense/", "confidence": 0.9}')
        http = FakeHTTP(pages)
        o = O.observe({"url": "https://aero.example.com/", "prefilter": True, "found_via": {"layer": "layer1"}}, http,
                      use_feedsearch=False)
        self.assertNotEqual(o.get("delivery_url"), "https://aero.example.com/feed")
        self.assertNotIn("https://aero.example.com/feed/", http.calls)  # no root guesses for a judged section


class TypeCountry(unittest.TestCase):
    def tearDown(self):
        A.set_base(set(), set())

    def test_types(self):
        A.set_base({"saab.com"}, set())
        self.assertEqual(A.guess_type({}, "saab.com", "https://saab.com/newsroom", "Saab news")[0], "company_news")
        self.assertEqual(A.guess_type({}, "defmin.fi", "https://defmin.fi/x", "Ajankohtaista")[0], "agency")
        self.assertEqual(A.guess_type({}, "defensie.nl", "https://defensie.nl/nieuws", None)[0], "agency")
        self.assertEqual(A.guess_type({}, "bundeswehr-journal.de", "https://bundeswehr-journal.de/feed",
                                      "Bundeswehr-Journal")[0], "media")
        self.assertEqual(A.guess_type({}, "x.example", "https://x.example/", "Federal Ministry of Defence")[0], "agency")

    def test_country_by_language(self):
        self.assertEqual(A.detect_country("adriadefense.com", None, None, "hr"), ("HR", "language"))
        self.assertEqual(A.detect_country("defence-blog.com", None, None, "en")[0], "international")
        self.assertEqual(A.detect_country("milmag.pl", None, None, "en")[0], "PL")  # the domain still wins


class Audit(unittest.TestCase):
    def accepted(self, conn, url, delivery):
        obs = {"candidate": {"url": url, "found_via": {"layer": "layer1"}}, "fetch": {"status": 200, "final_url": url},
               "robots": {"state": "rules", "allowed": True}, "delivery": "feed", "delivery_url": delivery,
               "items": [{"title": t, "date": "2026-11-18T08:00:00+00:00", "url": f"{url}a/{i}"} for i, t in enumerate(DEF)],
               "html_lang": "en"}
        counts, _ = A.admit(conn, [obs])
        self.assertEqual(counts["accept"], 1)
        return obs

    def test_audit_moves_a_bad_delivery_and_demotes_a_general_site(self):
        with tempfile.TemporaryDirectory() as d:
            conn = A.open_db(str(Path(d) / "r.sqlite"))
            obs = self.accepted(conn, "https://dpress.example/", "https://dpress.example/comments/feed")
            self.accepted(conn, "https://news.example/", "https://news.example/feed")
            # not in the audit: an accepted source stays decided
            self.assertEqual(A.admit(conn, [obs])[0]["skipped_decided"], 1)
            # audit 1: the right feed now -> the old channel is replaced
            good = {**obs, "delivery_url": "https://dpress.example/feed"}
            # audit 2: the judge says general -> the accepted channel leaves the list
            gen = {"candidate": {"url": "https://news.example/", "found_via": {"layer": "audit"}},
                   "fetch": {"status": 200, "final_url": "https://news.example/"}, "robots": {"state": "rules",
                   "allowed": True}, "general_outlet": {"judge": "fake"}, "items": []}
            # audit 3: a site that does not answer keeps its place
            down = {"candidate": {"url": "https://dpress.example/"}, "fetch": {"status": "timeout"}, "robots": {}}
            A.admit(conn, [good, gen], audit={"https://dpress.example/", "https://news.example/"})
            st = dict(conn.execute("SELECT canonical_url, status FROM source_state").fetchall())
            self.assertEqual(st["https://dpress.example/comments/feed"], "rejected")  # the wrong delivery
            self.assertEqual(st["https://dpress.example/feed"], "accepted")
            self.assertNotEqual(st["https://news.example/feed"], "accepted")
            before = dict(conn.execute("SELECT canonical_url, status FROM source_state").fetchall())
            A.admit(conn, [down], audit={"https://dpress.example/"})
            self.assertEqual(dict(conn.execute("SELECT canonical_url, status FROM source_state").fetchall()), before)
            conn.close()

    def test_audit_picks_old_accepts_and_skips_the_owners(self):
        import run_daily as R
        with tempfile.TemporaryDirectory() as d:
            conn = A.open_db(str(Path(d) / "r.sqlite"))
            for url, reasons, rules, record in (("https://old.example/", "[]", "a2sa-shadow-0.5", "{}"),
                                                ("https://owner.example/", '["owner_accept"]', "a2sa-shadow-0.5", "{}"),
                                                ("https://fresh.example/", "[]", A.RULES_VERSION, '{"judge": {}}')):
                conn.execute("INSERT INTO candidate_seen VALUES (?,?,?,?,?)", (url, "accept", "2026-11-18", 0, "ch-" + url))
                conn.execute("INSERT INTO admission_decision(candidate_url, channel_id, decision, reasons_json, "
                             "record_json, rules_version, decided_at) VALUES (?,?,?,?,?,?,?)",
                             (url, "ch-" + url, "accept", reasons, record, rules, "2026-11-18"))
            got = R.audit_candidates(conn)
            self.assertEqual([(c["url"], ch) for c, ch in got], [("https://old.example/", "ch-https://old.example/")])
            self.assertTrue(got[0][0]["prefilter"])
            conn.close()


if __name__ == "__main__":
    unittest.main()
