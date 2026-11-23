import unittest
from unittest.mock import Mock
from urllib.error import HTTPError

from collectors.source_registry.search import fresh_evidence_trial as trial

QUOTE = "The government released the annual accounts on Thursday."
URL = "https://reports.example/actual"
CASE = {"id": "case", "region": "region", "query": "annual accounts",
        "domain": "reports.example", "quote": QUOTE, "archive": []}


class FreshEvidenceTrialTest(unittest.TestCase):
    def test_archive_hit_avoids_both_searches(self):
        case = dict(CASE, archive=[{"url": URL, "text": "Introduction " + QUOTE}])
        exa, tavily, read = Mock(), Mock(), Mock()
        result = trial.run([case], (("exa", exa), ("tavily", tavily)), read)
        self.assertEqual(result["cases"][0]["source"], "archive")
        self.assertEqual(result["search_calls"], {})
        for unused in (exa, tavily, read):
            unused.assert_not_called()

    def test_exa_hit_does_not_call_tavily(self):
        exa = Mock(return_value={"results": [{"url": URL, "snippet": "unused"}]})
        tavily = Mock()
        result = trial.run([CASE], (("exa", exa), ("tavily", tavily)), Mock(return_value=QUOTE))
        self.assertEqual(result["cases"][0]["evidence"], {"url": URL, "excerpt": QUOTE})
        self.assertEqual(result["search_calls"], {"exa": 1})
        tavily.assert_not_called()

    def test_fallback_reads_at_most_two_documents_per_provider(self):
        wrong_urls = [f"https://reports.example/{num}" for num in range(5)]
        exa = Mock(return_value={"results": [{"url": url, "snippet": QUOTE} for url in wrong_urls]})
        tavily = Mock(return_value={"results": [{"url": URL}]})
        read = Mock(side_effect=["Another period", "Another event", QUOTE])
        result = trial.run([CASE], (("exa", exa), ("tavily", tavily)), read)
        self.assertEqual(result["cases"][0]["source"], "tavily")
        self.assertEqual([call.args[0] for call in read.call_args_list], wrong_urls[:2] + [URL])
        self.assertEqual(result["search_calls"], {"exa": 1, "tavily": 1})

    def test_both_unavailable_preserves_stable_case_and_status(self):
        exa = Mock(side_effect=HTTPError(URL, 429, "discarded", {}, None))
        tavily = Mock(side_effect=RuntimeError("private"))
        read = Mock()
        result = trial.run([CASE], (("exa", exa), ("tavily", tavily)), read)
        self.assertEqual(result["cases"][0]["id"], CASE["id"])
        self.assertEqual(result["cases"][0]["status"], "WAITING_EVIDENCE")
        self.assertEqual(result["cases"][0]["attempts"], {"exa": "HTTP_429", "tavily": "UNAVAILABLE"})
        self.assertEqual(result["search_calls"], {"exa": 1, "tavily": 1})
        read.assert_not_called()

    def test_manifest_requires_distinct_cases_regions_and_publishers(self):
        cases = [dict(CASE, id=str(i), region=str(i % 3), domain=f"pub{i % 3}.example") for i in range(12)]
        trial.validate(cases)
        cases[-1]["id"] = cases[0]["id"]
        with self.assertRaisesRegex(ValueError, "duplicate"):
            trial.validate(cases)

    def test_manifest_rejects_archive_from_another_publisher(self):
        cases = [dict(CASE, id=str(i), region=str(i % 3), domain=f"pub{i % 3}.example") for i in range(12)]
        cases[0]["archive"] = [{"url": URL, "text": QUOTE}]
        with self.assertRaisesRegex(ValueError, "archive domain mismatch"):
            trial.validate(cases)


if __name__ == "__main__":
    unittest.main()
