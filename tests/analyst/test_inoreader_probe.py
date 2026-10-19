"""Offline tests for the EXP-013 Inoreader full-text probe: aggregates only, no text leaves the function."""
import importlib.util
import json
import pathlib
import unittest

path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "inoreader-fulltext-probe.py"
spec = importlib.util.spec_from_file_location("probe", path)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

LONG = "<p>" + "Rheinmetall expands ammunition capacity in Unterlüß. " * 60 + "</p>"


def item(content, href="https://www.example.com/a"):
    return {"summary": {"content": content}, "canonical": [{"href": href}], "title": "SECRET TITLE"}


class TestProbe(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(probe.classify(item(LONG))[0], "FULL")
        self.assertEqual(probe.classify(item("<p>Short teaser…</p>"))[0], "PARTIAL")
        self.assertEqual(probe.classify(item(LONG + "<a>Read 11 remaining paragraphs</a>"))[0], "PARTIAL")
        self.assertEqual(probe.classify(item(""))[0], "EMPTY")

    def test_summary_has_no_text_or_titles(self):
        out = probe.summarize([item(LONG), item("teaser [...]", "https://news.example.org/x"), item(None)])
        dumped = json.dumps(out)
        self.assertEqual(out["items"], 3)
        self.assertEqual(out["classes"], {"FULL": 1, "PARTIAL": 1, "EMPTY": 1})
        self.assertNotIn("Rheinmetall", dumped)
        self.assertNotIn("SECRET TITLE", dumped)
        self.assertEqual({d["domain"] for d in out["top_domains"]}, {"example.com", "news.example.org"})


if __name__ == "__main__":
    unittest.main()
