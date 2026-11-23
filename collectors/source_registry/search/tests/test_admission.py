"""A2SA shadow admission tests on fictional observations. Run: /opt/homebrew/bin/python3.13 test_admission.py"""
import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
import admission as A  # noqa: E402
import registry as R  # noqa: E402

NOW = dt.datetime(2026, 11, 18, 12, 0, tzinfo=dt.timezone.utc)
DEF = ["Army orders new artillery rounds", "Navy tests unmanned surface vessel", "Ministry of defence signs drone deal",
       "Missile defence radar delivered", "Air force selects new trainer jet"]
CIVIL = ["City council approves budget", "Local football club wins cup", "New bakery opens downtown",
         "Weather warning for the weekend", "School year starts on Monday"]


def items(titles, days_ago=1):
    return [{"title": t, "date": (NOW - dt.timedelta(days=days_ago + i)).isoformat()} for i, t in enumerate(titles)]


def obs(url="https://news.example.org/", **kw):
    o = {"candidate": {"url": url, "found_via": {"channel": "test"}},
         "fetch": {"status": 200, "final_url": url}, "robots": {"state": "rules", "allowed": True},
         "delivery": "feed", "delivery_url": url.rstrip("/") + "/feed", "items": items(DEF), "html_lang": "en",
         "judge": {"specialised": True, "by": "fixture"}}
    o.update(kw)
    return o


def nobody(canon, host):
    return None, None


class Decide(unittest.TestCase):
    def d(self, o, known=nobody):
        return A.decide(o, known, NOW)

    def test_accept_all_checks_pass(self):
        r = self.d(obs())
        self.assertEqual((r["decision"], r["language"], r["type"], r["delivery"]), ("accept", "en", "media", "feed"))
        self.assertEqual([c["check"] for c in r["checks"]], [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertLessEqual(len(r["sample_items"]), 5)

    def test_check1_unreachable_and_foreign_redirect(self):
        self.assertEqual(self.d(obs(fetch={"status": 404}))["reason_codes"], ["unreachable"])
        r = self.d(obs(fetch={"status": 200, "final_url": "https://other.example.com/"}))
        self.assertEqual((r["decision"], r["reason_codes"]), ("hold", ["redirect_other_domain"]))  # 0.8: nothing goes to the owner
        self.assertEqual(self.d(obs(fetch={"status": "http_only"}))["reason_codes"], ["http_only"])
        same_site = self.d(obs(fetch={"status": 200, "final_url": "https://www.news.example.org/en/"}))
        self.assertEqual(same_site["decision"], "accept")

    def test_check1_transient_retries_twice_then_rejects(self):
        self.assertEqual(self.d(obs(fetch={"status": 503}))["decision"], "retry")
        self.assertEqual(self.d(obs(fetch={"status": "timeout"}, prior_transient=1))["decision"], "retry")
        r = self.d(obs(fetch={"status": 503}, prior_transient=2))
        self.assertEqual((r["decision"], r["reason_codes"]), ("reject", ["unreachable"]))

    def test_check2_robots(self):
        self.assertEqual(self.d(obs(robots={"state": "rules", "allowed": False}))["reason_codes"], ["robots_denied"])
        self.assertEqual(self.d(obs(robots={"state": "unreadable", "code": "HTTP_403"}))["decision"], "retry")
        self.assertEqual(self.d(obs(robots={"state": "absent", "allowed": True}))["decision"], "accept")

    def test_type_from_site_name_in_local_languages(self):
        o = obs(candidate={"url": "https://news.example.org/", "found_via": {"channel": "company_website"}})
        for name, want in (("Univerzita Karlova", "research"), ("Hochschule Rhein-Waal", "research"),
                           ("Riigi Kaitseinvesteeringute Keskus", "agency"), ("Ministerstwo Obrony Narodowej", "agency"),
                           ("Acme Drones Ltd", "company_news")):
            o["site_name"] = name
            self.assertEqual(self.d(o)["type"], want, name)

    def test_page_too_large_is_held(self):
        r = self.d(obs(fetch={"status": "RESPONSE_SIZE"}))
        self.assertEqual((r["decision"], r["reason_codes"]), ("hold", ["page_too_large"]))

    def test_type_research_from_site_name(self):
        o = obs(candidate={"url": "https://news.example.org/", "found_via": {"channel": "company_website"}})
        self.assertEqual(self.d(o)["type"], "company_news")
        o["site_name"] = "Danish Technological Institute"
        self.assertEqual(self.d(o)["type"], "research")

    def test_check3_no_news_site_is_kept_on_hold(self):
        r = self.d(obs(delivery=None, delivery_url=None))
        self.assertEqual((r["decision"], r["reason_codes"], r["delivery_url"]), ("hold", ["no_news_site"], "https://news.example.org/"))
        r = self.d(obs(items=items(DEF[:1])))
        self.assertEqual((r["decision"], r["reason_codes"]), ("hold", ["delivery_without_items"]))

    def test_check4_language(self):
        r = self.d(obs(html_lang="de", judge={"by": "fixture", "specialised": False}))
        if A._ready is None:  # our rules: titles vs page tag disagree -> a person decides
            self.assertEqual((r["decision"], r["reason_codes"]), ("hold", ["language_uncertain"]))
        else:  # Lingua is confident about the titles; the page tag is only recorded
            self.assertEqual((r["decision"], r["language"]), ("accept", "en"))
            self.assertIn("tag:de", [c for c in r["checks"] if c["check"] == 4][0]["basis"])
        uk = ["Міністерство оборони замовило нові дрони", "Збройні сили отримали боєприпаси",
              "Військові випробували безпілотник", "Оборонна промисловість нарощує виробництво",
              "Уряд схвалив закупівлю озброєння"]
        r = self.d(obs(url="https://news.example.ua/", items=items(uk), html_lang="uk"))
        self.assertEqual((r["decision"], r["language"], r["country"]), ("accept", "uk", "UA"))

    def test_check5_country_never_rejects(self):
        self.assertEqual(self.d(obs())["country"], "international")
        r = self.d(obs(url="https://news.example.de/", candidate={"url": "https://news.example.de/", "guessed_country": "FR"}))
        self.assertEqual((r["decision"], r["country"]), ("accept", "international"))

    def test_six_unreviewed_channels_wait_with_exact_delivery(self):
        for n in range(6):
            url = f"https://agency{n}.gov.example/"
            for delivery in ("feed", "page"):
                with self.subTest(n=n, delivery=delivery):
                    rec = self.d(obs(url, delivery=delivery, judge=None))
                    self.assertEqual((rec["decision"], rec["reason_codes"]), ("hold", ["no_judge"]))
                    self.assertEqual(rec["delivery_url"], url + "feed")

    def test_owner_approval_still_requires_other_checks(self):
        approved = self.d(obs(judge=None, approval_reasons=["owner_accept"]))
        self.assertEqual((approved["decision"], approved["reason_codes"]), ("accept", ["owner_accept"]))
        blocked = self.d(obs(judge=None, approval_reasons=["owner_accept"], robots={"allowed": False}))
        self.assertEqual(blocked["reason_codes"], ["robots_denied"])

    def test_shared_gate_version_and_exact_owner_reason(self):
        self.assertTrue(A.can_poll("https://x.example/feed", "a2sa-shadow-0.10", {"by": "fixture"}, []))
        self.assertTrue(A.can_poll("https://x.example/feed", "a2sa-shadow-0.7", {"by": "fixture"}, []))
        self.assertFalse(A.can_poll("https://x.example/feed", "a2sa-shadow-0.6", {"by": "fixture"}, []))
        self.assertFalse(A.can_poll("https://x.example/feed", None, None, ["not_owner_accept"]))
        r = self.d(obs(url="https://news.example.pl/", candidate={"url": "https://news.example.pl/", "guessed_country": "PL"}))
        self.assertEqual(r["country"], "PL")

    def test_check6_duplicates(self):
        same = lambda c, h: ("same", "ch_1")
        host = lambda c, h: ("same_host", "ch_2")
        self.assertEqual(self.d(obs(), same)["reason_codes"], ["duplicate"])
        self.assertEqual(self.d(obs(), same)["decision"], "reject")
        self.assertEqual(self.d(obs(), host)["decision"], "accept")  # 0.8: another section of a known site
        self.assertEqual(self.d(obs(), host)["checks"][5]["basis"], "same_host_other_section")

    def test_check7_freshness(self):
        self.assertEqual(self.d(obs(items=items(DEF, days_ago=90)))["reason_codes"], ["stale"])
        undated = [{"title": t} for t in DEF]
        self.assertEqual(self.d(obs(items=undated))["reason_codes"], ["no_dates"])
        future = [{"title": t, "date": (NOW + dt.timedelta(days=30)).isoformat()} for t in DEF]
        self.assertEqual(self.d(obs(items=future))["reason_codes"], ["no_dates"])

    def test_check8_topic(self):
        self.assertEqual(self.d(obs(items=items(CIVIL)))["reason_codes"], ["topic_below_threshold"])
        self.assertEqual(self.d(obs(items=items(DEF[:1] + CIVIL[:4])))["decision"], "accept")  # 1/5 = 20%
        self.assertEqual(self.d(obs(items=items(DEF[:3])))["reason_codes"], ["topic_sample_too_small"])
        space = ["Satellite launch scheduled", "Space agency budget", "Cyber week opens", "Satellite imagery app", "Space tourism"]
        self.assertEqual(self.d(obs(items=items(space)))["reason_codes"], ["topic_below_threshold"])
        tender = obs(url="https://tenders.example.org/", items=items(CIVIL),
                     candidate={"url": "https://tenders.example.org/", "guessed_type": "procurement"})
        r = self.d(tender)
        self.assertEqual((r["decision"], r["checks"][-1]["result"]), ("accept", "skipped"))

    def test_first_failure_stops(self):
        r = self.d(obs(robots={"state": "rules", "allowed": False}, items=items(CIVIL)))
        self.assertEqual([c["check"] for c in r["checks"]], [1, 2])


class Cycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = A.open_db(str(Path(self.tmp.name) / "r.sqlite"))
        rec = R.Record("sd_csv", "1", "Known", "https://known.example.org/", "https://known.example.org/rss")
        R.import_records(self.conn, [rec])

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_cycle_writes_states_and_is_idempotent(self):
        batch = [obs(), obs("https://civil.example.org/", items=items(CIVIL)),
                 obs("https://known.example.org/", delivery_url="https://known.example.org/rss"),
                 obs("https://known.example.org/news/", delivery_url="https://known.example.org/news/feed"),
                 obs("https://down.example.org/", fetch={"status": 404})]
        counts, _ = A.admit(self.conn, batch, NOW)
        self.assertEqual((counts["accept"], counts["general"], counts["disputed"], counts["reject"]), (2, 1, 0, 2))
        states = dict(self.conn.execute("SELECT canonical_url, status FROM source_state").fetchall())
        self.assertEqual(states["https://news.example.org/feed"], "accepted")
        self.assertEqual(states["https://civil.example.org/feed"], "general_outlet")  # rules 0.5
        again, _ = A.admit(self.conn, batch, NOW + dt.timedelta(days=1))
        self.assertEqual((again["accept"], again["skipped_decided"], again["skipped_recent_reject"]), (0, 3, 2))
        later, _ = A.admit(self.conn, [batch[4]], NOW + dt.timedelta(days=91))
        self.assertEqual(later["reject"], 1)

    def test_accepted_source_becomes_known(self):
        A.admit(self.conn, [obs()], NOW)
        counts, _ = A.admit(self.conn, [obs("https://news.example.org/?utm_source=x")], NOW)
        self.assertEqual(counts["skipped_decided"], 1)  # same candidate after canonical form
        other = obs("https://www.news.example.org/en", delivery_url="https://news.example.org/feed?utm_medium=rss")
        counts, res = A.admit(self.conn, [other], NOW)
        self.assertEqual(res[0]["reason_codes"], ["duplicate"])

    def test_retry_counts_across_cycles(self):
        down = obs("https://slow.example.org/", fetch={"status": 503})
        for day, want in ((0, "retry"), (1, "retry"), (2, "reject")):
            counts, res = A.admit(self.conn, [down], NOW + dt.timedelta(days=day))
            self.assertEqual(res[0]["decision"], want)

    def test_no_news_kept_not_in_owner_batch(self):
        counts, _ = A.admit(self.conn, [obs("https://plain.example.org/", delivery=None, delivery_url=None)], NOW)
        self.assertEqual(counts["hold"], 1)
        self.assertEqual(self.conn.execute("SELECT status FROM source_state").fetchone()[0], "no_news")
        self.assertEqual(A.disputed_batch(self.conn), [])
        again, _ = A.admit(self.conn, [obs("https://plain.example.org/", delivery=None, delivery_url=None)], NOW)
        self.assertEqual(again["accept"] + again["disputed"], 0)  # nothing decided twice

    def test_disputed_batch_for_owner(self):
        A.admit(self.conn, [obs("https://civil.example.org/", items=items(CIVIL))], NOW)
        self.assertEqual(A.disputed_batch(self.conn), [])  # rules 0.5: a general outlet is not the owner's
        A.admit(self.conn, [obs("https://lang.example.org/", items=items(["x y z", "a b", "1 2"] * 2))], NOW)
        self.assertEqual(A.disputed_batch(self.conn), [])  # 0.8: uncertain language is held, not the owner's

    def test_failed_write_leaves_no_partial_state(self):
        class Boom(Exception):
            pass
        real = A.registry.channel_id_for
        A.registry.channel_id_for = lambda c: (_ for _ in ()).throw(Boom())
        try:
            with self.assertRaises(Boom):
                A.admit(self.conn, [obs()], NOW)
        finally:
            A.registry.channel_id_for = real
        self.assertEqual(self.conn.execute("SELECT count(*) FROM admission_decision").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM candidate_seen").fetchone()[0], 0)


class Rules05(unittest.TestCase):
    """D-20261118-A1SD-RETRO-PLAN-G2: the owner sees only what a rule cannot decide."""

    def d(self, o):
        return A.decide(o, lambda c, h: (None, None), NOW)

    def test_general_hold_and_official_undated(self):
        self.assertEqual(self.d(obs(items=items(CIVIL)))["decision"], "general")
        self.assertEqual(self.d(obs(items=items(DEF, days_ago=90)))["decision"], "hold")
        self.assertEqual(self.d(obs(items=items(DEF[:3])))["decision"], "hold")
        undated = [{"url": f"https://news.example.org/{i}", "title": t, "date": None} for i, t in enumerate(DEF)]
        self.assertEqual(self.d(obs(items=undated))["decision"], "hold")
        official = obs("https://www.mod.example.gov/", items=undated, delivery_url="https://www.mod.example.gov/news")
        self.assertEqual(self.d(official)["decision"], "hold")  # no sure judge verdict: still waits
        self.assertEqual(A.status_of({"decision": "hold", "reason_codes": ["stale"]}), "on_hold")
        self.assertEqual(A.status_of({"decision": "hold", "reason_codes": ["no_news_site"]}), "no_news")


class Rules09(unittest.TestCase):
    """A1SD-04 (21.11): a sure judge overrules the keyword count for non-media kinds; undated pages are accepted."""

    def test_sure_judge_overrules_keywords_for_funds_not_for_media(self):
        sure = {"specialised": True, "confidence": 0.9, "type": "investment", "by": "fake"}
        fund = obs("https://fund.example.org/", items=items(CIVIL), judge=sure,
                   candidate={"url": "https://fund.example.org/", "guessed_type": "investment"})
        r = A.decide(fund, nobody, NOW)
        self.assertEqual(r["decision"], "accept")
        self.assertEqual(r["checks"][-1]["basis"], "judge_sure_overrules_keywords")
        self.assertEqual([c["check"] for c in r["checks"]].count(8), 1)
        media = obs(items=items(CIVIL), judge={**sure, "type": "media"})
        self.assertEqual(A.decide(media, nobody, NOW)["decision"], "general")
        unsure = obs("https://fund.example.org/", items=items(CIVIL), judge={**sure, "confidence": 0.6},
                     candidate={"url": "https://fund.example.org/", "guessed_type": "investment"})
        self.assertEqual(A.decide(unsure, nobody, NOW)["decision"], "general")

    def test_undated_page_accepted_with_sure_judge_feed_still_held(self):
        undated = [{"url": f"https://www.mod.example.gov/n/{i}", "title": t, "date": None} for i, t in enumerate(DEF)]
        sure = {"specialised": True, "confidence": 0.85, "by": "fake"}
        page = obs("https://www.mod.example.gov/", items=undated, delivery="page",
                   delivery_url="https://www.mod.example.gov/news", judge=sure)
        r = A.decide(page, nobody, NOW)
        self.assertEqual(r["decision"], "accept")
        self.assertIn("undated_page_judge_sure", [c.get("basis") for c in r["checks"]])
        feed = obs("https://www.mod.example.gov/", items=undated, judge=sure)
        self.assertEqual(A.decide(feed, nobody, NOW)["reason_codes"], ["no_dates"])


class NewLanguages(unittest.TestCase):
    def test_defence_titles_in_new_languages_count_civil_do_not(self):
        defence = ["Νέα σύμβαση για την άμυνα της χώρας", "A Honvédség új harckocsikat kap", "Exército português recebe drones",
                   "Министерството на отбраната подписа договор", "Hrvatska vojska nabavlja nove helikoptere",
                   "Lietuvos kariuomenė gavo naujų ginklų", "Latvijas bruņotie spēki saņem jaunus radarus",
                   "Kaitseväe uus relvastus", "Forsvaret kjøper nye militære kjøretøy"]
        civil = ["Νέα στρατηγική για τον τουρισμό", "Védelmi tanács a vízügyben", "Football results of the weekend"]
        self.assertEqual(A.topic_share(defence)[0], 1.0)
        self.assertEqual(A.topic_share(civil)[0], 0.0)


class Rules08(unittest.TestCase):
    """Owner 20.11 "Не нужно, решай сам": no disputed decision; the judge settles an uncertain language."""

    def test_uncertain_language_accepted_when_judge_says_specialised(self):
        o = obs(items=items(["x y z", "a b", "1 2"] * 2))
        o["judge"] = {"specialised": True, "by": "fake"}
        o["items"] = [{**i, "title": "Army drones radar missile " + i["title"]} for i in o["items"]]
        r = A.decide(o, lambda c, h: (None, None), NOW)
        self.assertNotEqual(r["decision"], "disputed")
        self.assertEqual(r["checks"][3]["basis"], "judge_specialised")

    def test_old_disputed_are_rechecked_first(self):
        import run_daily as R
        with tempfile.TemporaryDirectory() as d:
            conn = A.open_db(str(Path(d) / "r.sqlite"))
            conn.execute("INSERT INTO candidate_seen VALUES (?,?,?,?,?)", ("https://d.example/", "disputed", NOW.isoformat(), 0, None))
            self.assertEqual([c["url"] for c in R.recheck_candidates(conn)], ["https://d.example/"])
            o = obs("https://d.example/", fetch={"status": "http_only"})
            counts, _ = A.admit(conn, [o], NOW, recheck={"https://d.example/"})
            self.assertEqual(counts["hold"], 1)
            # 0.9: a keyword-general site of older rules is judged again; one of the current rules is not
            for url, rules in (("https://g.example/", "a2sa-shadow-0.8"), ("https://n.example/", A.RULES_VERSION)):
                conn.execute("INSERT INTO candidate_seen VALUES (?,?,?,?,?)", (url, "general", NOW.isoformat(), 0, None))
                conn.execute("INSERT INTO admission_decision(candidate_url, channel_id, decision, reasons_json, "
                             "record_json, rules_version, decided_at) VALUES (?,?,?,?,?,?,?)",
                             (url, None, "general", '["topic_below_threshold"]', "{}", rules, NOW.isoformat()))
            urls = [c["url"] for c in R.recheck_candidates(conn)]
            self.assertIn("https://g.example/", urls)
            self.assertNotIn("https://n.example/", urls)
            conn.close()


if __name__ == "__main__":
    unittest.main()
