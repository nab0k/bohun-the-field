"""A5TC-02 v2: headline relaxations, lead merge, completeness, read_one integration (fictional pages)."""
import json
import unittest

from collectors.kb_pilot import fulltext
from collectors.kb_pilot.article_identity import compare_headline, compare_headline_rule, lead_merge
from collectors.kb_pilot.body_selection import select_body
from collectors.kb_pilot.completeness import assess, strip_credits

LEAD = "The city library will stay open later on Saturdays this spring, the board said on Monday."
PARAS = [
    "Visitors can borrow books, use the reading room and collect reserved items during the new weekend hours.",
    "Staff will record visitor numbers during the first month and a printed timetable will be posted at the entrance.",
    "The library board will review the trial in June and readers can leave comments at the desk or send them by post.",
    "Existing weekday hours remain the same, and the children's reading area keeps its current schedule this season.",
    "The board thanked volunteers who helped prepare the extended hours and said further changes would be announced.",
]
BODY = "\n".join(PARAS)


def page(head, lead="", body=BODY, extra=""):
    paras = "".join(f"<p>{p}</p>" for p in body.split("\n"))
    return (f'<html><head><meta property="og:title" content="{head}">{extra}</head><body><h1>{head}</h1>'
            f'<div class="lead">{lead}</div><article>{paras}</article></body></html>').encode()


class Headline(unittest.TestCase):
    def test_strict_cases_unchanged(self):
        self.assertEqual(compare_headline("Council will open three rooms", page("Council will open four rooms")),
                         "publisher_title_mismatch")
        self.assertEqual(compare_headline("Council will open three rooms", page("Council will not open three rooms")),
                         "publisher_title_mismatch")
        self.assertEqual(compare_headline_rule("Library hours extended", page("Library hours extended"))[1], "strict")

    def test_relaxations(self):
        self.assertEqual(compare_headline_rule("Fire near depot &ndash; media", page("Fire near depot – media")),
                         ("ok", "unescaped_title"))
        self.assertEqual(compare_headline_rule("Guidance: Library opening rules (JSP 1)", page("Library opening rules (JSP 1)")),
                         ("ok", "govuk_type_prefix"))
        self.assertEqual(compare_headline_rule("City library extends weekend hours",
                                               page("GAZETTE | Culture: City library extends weekend hours")),
                         ("ok", "site_or_rubric_prefix"))

    def test_relaxations_stay_narrow(self):
        self.assertNotEqual(compare_headline("Rumour: Library opening rules", page("Library opening rules")), "ok")
        self.assertNotEqual(compare_headline("City library extends weekend hours",
                                             page("GAZETTE | City library extends weekend hours again")), "ok")


class Lead(unittest.TestCase):
    def test_merge_only_when_lead_on_page(self):
        self.assertEqual(lead_merge(LEAD, BODY, page("H", LEAD)), (LEAD + "\n" + BODY, "provider_content_capture"))
        self.assertEqual(lead_merge(LEAD, BODY, page("H", "")), (BODY, None))
        self.assertEqual(lead_merge(LEAD, LEAD + "\n" + BODY, page("H", LEAD)), (LEAD + "\n" + BODY, None))
        self.assertEqual(lead_merge("Too short teaser.", BODY, page("H", "Too short teaser.")), (BODY, None))


class Completeness(unittest.TestCase):
    def test_states(self):
        full = LEAD + "\n" + BODY
        self.assertEqual(assess("City library weekend hours", full, page("H", LEAD), "ok", LEAD)["state"], "full")
        self.assertIn("lead_missing", assess("City library weekend hours", BODY, page("H", LEAD), "ok", LEAD)["reasons"])
        self.assertEqual(assess("x", "", None, None)["state"], "none")
        self.assertEqual(assess("City library weekend hours", BODY, None, "publisher_title_mismatch")["state"], "mismatch")
        self.assertEqual(strip_credits("End.\nPhoto: Someone"), "End.")

    def test_paywall_and_document_landing(self):
        paywalled = page("H", extra='<script type="application/ld+json">{"isAccessibleForFree": false}</script>')
        self.assertIn("paywalled_jsonld", assess("City library weekend hours", LEAD + "\n" + BODY, paywalled, "ok")["reasons"])
        doc = page("Rules", body=BODY) + b'<a href="/f/rules.pdf">PDF</a>'
        self.assertEqual(assess("Guidance: Rules", LEAD + "\n" + BODY, doc, "ok")["reasons"], ["document_in_attachment"])


class ReadOne(unittest.TestCase):
    URL = "https://news.example.org/a/1"

    def run_one(self, title, html, teaser):
        def fetch(url, allowed, **kw):
            if url.endswith("/robots.txt"):
                return {"body": b"User-agent: *\nDisallow:\n"}
            return {"url": url, "status": 200, "body": html}
        raw = {"content_capture": {"version": 1, "provider_field": "summary.content",
                                   "status": "BODY_PRESENT_COMPLETENESS_UNVERIFIED", "text": teaser}}
        item = {"url": self.URL, "title": title, "provider_chars": len(teaser), "raw": raw}
        extract = lambda body, url: BODY  # the extractor drops the lead, as observed on Ukrinform
        return fulltext.read_one(item, fulltext.Robots(fetch), fetch, extract, lambda host: None)

    def test_lead_restored_and_quality_recorded(self):
        reason, cap = self.run_one("City library weekend hours &ndash; update",
                                   page("City library weekend hours – update", LEAD), LEAD)
        self.assertEqual(reason, "ok")
        self.assertTrue(cap["text"].startswith(LEAD))
        self.assertEqual((cap["lead_source"], cap["identity_rule"]), ("provider_content_capture", "unescaped_title"))
        self.assertEqual(cap["quality"]["state"], "full")
        self.assertEqual(cap["completeness"], "UNKNOWN")  # body_selection contract unchanged
        self.assertEqual(select_body({"publisher_capture": cap}, self.URL).source_kind, "publisher_article")
        json.dumps(cap)  # stays JSON-serialisable for the jsonb write

    def test_wrong_page_still_rejected(self):
        reason, cap = self.run_one("Apple growers prepare harvest", page("City library weekend hours", LEAD), LEAD)
        self.assertEqual((reason, cap), ("publisher_title_mismatch", None))


if __name__ == "__main__":
    unittest.main()


class Fixes20261118(unittest.TestCase):
    """Found on the live day 16.11 (EXP-034): complete texts marked partial."""

    def test_credit_variants_and_photo_sale_line(self):
        for tail in ("Illustrative photo: State Emergency Service", "Archive photo: DTEK",
                     "Photo: Ministry of Defense", "Ukrinform photos can be purchased here"):
            self.assertEqual(strip_credits("Body ends here.\n" + tail), "Body ends here.")

    def test_embedded_tweet_at_end(self):
        text = "Body ends here.\nLosses: 3 aircraft https://t.co/abc123\n— Some Account (@acct) November 15, 2026"
        self.assertEqual(strip_credits(text), "Body ends here.")

    def test_complete_short_news_with_lead(self):
        short = LEAD + "\n" + "\n".join(PARAS[:3])  # ~420 chars: below 600, lead inside, 4 paragraphs
        self.assertLess(len(short), 600)
        self.assertGreaterEqual(len(short), 400)
        self.assertEqual(assess("City library weekend hours", short, page("H", LEAD), "ok", LEAD)["state"], "full")

    def test_short_text_still_partial_without_lead_or_too_short(self):
        short = "\n".join(PARAS[:4])
        self.assertIn("short_text", assess("City library weekend hours", short, None, "ok", "")["reasons"])
        tiny = LEAD + "\n" + PARAS[0]
        self.assertIn("short_text", assess("City library weekend hours", tiny, page("H", LEAD), "ok", LEAD)["reasons"])

    def test_unknown_last_line_still_unfinished(self):
        self.assertIn("no_terminal_punctuation",
                      assess("City library weekend hours", LEAD + "\n" + BODY + "\nAnd then the board decided to",
                             None, "ok", LEAD)["reasons"])
