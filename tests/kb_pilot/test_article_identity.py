import unittest

from collectors.kb_pilot.article_identity import compare_headline


class HeadlineTest(unittest.TestCase):
    def test_small_editorial_change_and_declared_site_suffix(self):
        page = b'<meta property="og:site_name" content="City Gazette"><h1>The city library extends weekend hours | City Gazette</h1>'
        self.assertEqual(compare_headline("City library extends weekend hours", page), "ok")

    def test_mismatch_even_if_fallback_heading_matches(self):
        page = b'<meta property="og:title" content="Apple growers prepare for harvest"><h1>City library updates hours</h1>'
        self.assertEqual(compare_headline("City library updates hours", page), "publisher_title_mismatch")

    def test_changes_to_negation_quantity_and_unknown_suffix_fail(self):
        for headline in (
            "Council will not open three reading rooms",
            "Council will open four reading rooms",
            "Council will open three reading rooms — corrections",
        ):
            with self.subTest(headline=headline):
                page = f"<h1>{headline}</h1>".encode()
                self.assertEqual(compare_headline("Council will open three reading rooms", page), "publisher_title_mismatch")

    def test_punctuation_case_entities_and_soft_hyphen(self):
        page = "<title>Публічна бібліо\u00adтека &amp; архів</title>".encode()
        self.assertEqual(compare_headline("ПУБЛІЧНА БІБЛІОТЕКА — АРХІВ", page), "ok")

    def test_unknown_input_and_output(self):
        self.assertEqual(compare_headline("", b"<h1>Title</h1>"), "incoming_title_missing")
        self.assertEqual(compare_headline("Title", b"<p>Article</p>"), "publisher_title_missing")
        self.assertEqual(compare_headline("Title", b"\xff"), "publisher_title_unreadable")
