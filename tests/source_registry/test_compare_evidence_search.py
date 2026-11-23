import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from collectors.source_registry.search import compare_evidence_search as trial


class EvidenceSearchTest(unittest.TestCase):
    def test_same_queries_and_bounded_results(self):
        calls = {"tavily": [], "exa": []}

        def search(name):
            def fake(job):
                calls[name].append(job)
                return {"results": [{"url": f"https://www.gov.uk/path/{i}"} for i in range(7)]}
            return fake

        results = trial.compare(routes={name: search(name) for name in calls})
        self.assertEqual(len(results), 12)
        self.assertEqual(len(calls["tavily"]), 12)
        self.assertEqual(calls["tavily"], calls["exa"])
        self.assertTrue(all(job["max_results"] == 5 for job in calls["tavily"]))
        self.assertEqual(len(results[0]["providers"]["exa"]["urls"]), 5)

    def test_saved_corpus_skips_searches(self):
        covered = [case_id for case_id, _, _ in trial.CASES]
        with patch.object(trial.providers, "tavily_api") as tavily, patch.object(trial, "exa_lite") as exa:
            self.assertTrue(all(row["status"] == "CORPUS_COVERED" for row in trial.compare(covered)))
        tavily.assert_not_called()
        exa.assert_not_called()

    def test_errors_do_not_expose_responses_or_retry(self):
        with patch.object(trial.providers, "tavily_api", side_effect=HTTPError("", 429, "discarded", {}, None)) as tavily:
            with patch.object(trial, "exa_lite", return_value={"results": None}) as exa:
                results = trial.compare()
        self.assertEqual(tavily.call_count, 12)
        self.assertEqual(exa.call_count, 12)
        self.assertEqual(results[0]["providers"], {"tavily": {"status": "HTTP_429"}, "exa": {"status": "UNAVAILABLE"}})

    def test_official_url_filter_and_deduplication(self):
        urls = ["https://www.gov.uk/a", "https://www.gov.uk/a", "https://gov.uk.evil.test/a",
                "https://account@gov.uk/a", "http://gov.uk/a"]
        self.assertEqual(trial.official_candidates({"results": [{"url": url} for url in urls]}, "gov.uk"),
                         ["https://www.gov.uk/a"])

    def test_exa_uses_lite_api_without_add_ons(self):
        with patch.object(trial.providers, "_key", return_value="fixture-key"):
            with patch.object(trial.providers, "_post", return_value={"results": []}) as post:
                trial.exa_lite({"query": "public question", "max_results": 5})
        post.assert_called_once_with(
            "https://api.exa.ai/search", {"query": "public question", "type": "lite", "numResults": 5},
            {"x-api-key": "fixture-key"}, timeout=20,
        )


if __name__ == "__main__":
    unittest.main()
