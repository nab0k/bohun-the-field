"""KB_RESEARCH=free: research from free search and a free model, never from paid Gemini (HTTP 402 on 23.11.2026)."""
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from collectors.kb_pilot import free_research, pilot
from collectors.kb_pilot.free import FreeModel
from collectors.kb_pilot.free_research import FreeSearch, exa, tavily
from collectors.kb_pilot.gemini import Gemini, Ledger
from tests.kb_pilot.test_pilot import (DOCTRINE, ITEMS, RESEARCH, SHORT, FakeCursor, Scripted, fake_pages, one_group,
                                       openai_reply)


def paid_402(url, headers, body):
    raise urllib.error.HTTPError(url, 402, "Your prepayment credits are depleted.", {}, None)


SOURCES = [{"uri": "https://www.defense.gov/award", "title": "award", "text": "Multiyear award for PAC-3 MSE."}]


class SearchTest(unittest.TestCase):
    def test_exa_then_tavily_and_cap(self):
        calls = []

        def exa_down(q):
            calls.append("exa"); raise urllib.error.HTTPError("u", 429, "busy", {}, None)

        def tav(q):
            calls.append("tavily"); return SOURCES
        s = FreeSearch([("exa", exa_down), ("tavily", tav)], cap=1)
        self.assertEqual(s("PAC-3"), SOURCES)
        self.assertEqual(s("again"), [])                       # cap reached: no call, no error
        self.assertEqual(calls, ["exa", "tavily"])
        self.assertEqual(s.stats["errors"], {"exa_http_429": 1})
        self.assertEqual(s.stats["skipped_cap"], 1)

    def test_no_keys_means_no_search(self):
        s = FreeSearch.from_env(env={})
        self.assertEqual(s("x"), [])

    def test_provider_shapes(self):
        seen = []

        def t(url, headers, body):
            seen.append((url, sorted(headers), body))
            if "exa" in url:
                return {"results": [{"url": "https://a", "title": "A", "text": "t" * 5000}, {"title": "no url"}]}
            return {"results": [{"url": "https://b", "title": "B", "content": "c"}]}
        self.assertEqual(exa("k", "q", t), [{"uri": "https://a", "title": "A", "text": "t" * 1200}])
        self.assertEqual(tavily("k", "q", t), [{"uri": "https://b", "title": "B", "text": "c"}])
        self.assertEqual(seen[0][1], ["x-api-key"])
        self.assertEqual(seen[1][1], ["Authorization"])


class NoPaidTest(unittest.TestCase):
    def test_unanswered_request_fails_alone_when_paid_is_off(self):
        def busy(url, headers, body):
            raise urllib.error.HTTPError(url, 429, "busy", {}, None)
        gem = Gemini("k", Ledger(), paid_402)
        cheap = FreeModel(gem, account="a", token="t", transport=busy, paid=False)
        out = cheap.many([cheap.request("s", {}, {"type": "object"}, step="writer")])
        self.assertEqual(out[0][0], "error")
        self.assertEqual(cheap.stats["fallback"]["paid_off"], 1)
        self.assertEqual(gem.ledger.calls, 0)


class FreeRouteTest(unittest.TestCase):
    def run_free(self, sources):
        gem = Gemini("k", Ledger(), paid_402)                          # any paid call would stop the run
        free_t = Scripted([openai_reply(one_group()), openai_reply(RESEARCH), openai_reply(SHORT)])
        cheap = FreeModel(gem, account="a", token="t", transport=free_t, paid=False)
        searcher = FreeSearch([("exa", lambda q: sources)], cap=5)
        with tempfile.TemporaryDirectory() as d:
            result = pilot.run(FakeCursor(), gem, ITEMS, {}, DOCTRINE, Path(d), fetcher=fake_pages, cheap=cheap,
                               searcher=searcher)
        return result, free_t, gem

    def test_free_route_reaches_candidate_without_paid_calls(self):
        result, free_t, gem = self.run_free(SOURCES)
        self.assertIsNone(result.get("stop"))
        ev = result["events"][0]
        self.assertEqual(ev["outcome"], "CANDIDATE", ev.get("reasons"))
        self.assertEqual(gem.ledger.calls, 0)
        self.assertEqual(result["research_mode"], "free")
        self.assertEqual(result["free_search"]["by_provider"], {"exa": 1})
        packet = json.loads(free_t.bodies[1]["messages"][1]["content"].split("\n\n", 1)[1])
        self.assertEqual(packet["web_sources"][0]["url"], "https://www.defense.gov/award")
        self.assertIn("do not answer from memory", free_t.bodies[1]["messages"][0]["content"])
        statuses = {f["id"]: f["status"] for f in ev["facts"]}
        self.assertEqual(statuses["T4"], "source_unreachable")       # the code still checks every cited page

    def test_search_failure_does_not_stop_the_run(self):
        result, _, gem = self.run_free([])
        self.assertIsNone(result.get("stop"))
        self.assertEqual(gem.ledger.calls, 0)
        self.assertEqual(free_research.info([], False)["search_queries"], 0)


if __name__ == "__main__":
    unittest.main()
