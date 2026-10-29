import json
import tempfile
import unittest
from pathlib import Path

from collectors.kb_pilot import pilot
from collectors.kb_pilot.gemini import Gemini, Ledger, parse_json_text
from collectors.kb_pilot.pages import amount_forms, check_row, html_to_markdown, is_official, sections
from collectors.synthesis.live import BudgetStop

PAGE = """<html><head><title>PAC-3 contract</title><script>x()</script></head><body><nav>menu</nav>
<h1>Award</h1><p>On 29 July 2026 the Department awarded $58.62 billion for PAC-3 MSE.</p>
<h2>Motors</h2><p>Northrop Grumman becomes a second source of rocket motors in 2026.</p></body></html>"""


def usage(i=100, o=50, tool=0):
    return {"promptTokenCount": i, "candidatesTokenCount": o, "thoughtsTokenCount": 10, "toolUsePromptTokenCount": tool}


def reply(obj, queries=0, **u):
    cand = {"finishReason": "STOP", "content": {"parts": [{"text": json.dumps(obj)}]},
            "groundingMetadata": {"webSearchQueries": ["q"] * queries,
                                  "groundingChunks": [{"web": {"uri": "https://x", "title": "x"}}]}}
    return {"candidates": [cand], "usageMetadata": usage(**u)}


ITEMS = [
    {"event_id": 1, "type": "contract", "topic": "us-munitions-ramp-up", "item_id": 11, "title": "Poland to host PAC-3 centre",
     "url": "https://news.example/a", "published": "2026-10-27T10:00:00+00:00", "outlet": "EDR", "text": "Poland PAC-3 centre."},
    {"event_id": 2, "type": "contract", "topic": "us-munitions-ramp-up", "item_id": 12, "title": "Polen erhält PAC-3 Zentrum",
     "url": "https://news.example/b", "published": "2026-10-27T12:00:00+00:00", "outlet": "ES&T", "text": "Polen PAC-3."},
]

RESEARCH = {
    "primary_source": {"url": "https://www.lockheedmartin.com/pr", "title": "LM release", "publisher": "Lockheed Martin",
                       "date": "2026-10-27"},
    "parent": {"name": "PAC-3 MSE ramp-up", "summary": "US triples output."},
    "timeline": [
        {"date": "2026-07-29", "precision": "day", "what": "Multiyear award.", "commitment": "ceiling",
         "amount": 58620000000, "currency": "USD", "source_url": "https://www.defense.gov/award"},
        {"date": "2026-08-03", "precision": "day", "what": "Second motor source.", "commitment": "framework",
         "source_url": "https://www.defense.gov/award"},
        {"date": "2018-03-01", "precision": "month", "what": "Wisla phase I.", "commitment": "contract", "archive_id": 900},
        {"date": "2025-10-14", "precision": "day", "what": "Diehl MoU.", "commitment": "framework",
         "source_url": "https://dead.example/x"},
    ],
    "supply_chain": [{"upper": "PAC-3 MSE", "lower": "rocket motors", "level": "component", "note": "sole source until 2026",
                      "source_url": "https://www.defense.gov/award"}],
    "restrictions": [],
    "documents": [{"title": "Award notice", "url": "https://www.defense.gov/award", "doc_type": "contract_notice",
                   "issuer": "DoD", "date": "2026-07-29"}],
}

DOCTRINE = [{"id": "US-03", "jurisdiction": "US", "title": "FY2027 budget", "document_date": "2026-04-01",
             "direction": "Multiyear munitions contracts.", "topics": ["us-munitions-ramp-up"], "verified": True,
             "source_url": "https://www.defense.gov/budget"}]

WRITER = {"headline": "Poland to host Europe's only PAC-3 centre", "lead": "Europe gets sustainment first.",
          "what_happened": [{"text": "Poland will host the centre.", "refs": ["N1"]}],
          "context": [{"text": "In July the US awarded up to $58.62 billion.", "refs": ["T1"]}],
          "policy_direction": [{"text": "The US buys missiles through multiyear contracts.", "refs": ["D1"]}],
          "bigger_picture": [{"text": "Supply becomes transatlantic.", "refs": ["T1", "T2"]}],
          "supply_chain": [{"text": "Rocket motors were sole-sourced.", "refs": ["S1"]}],
          "why_it_matters": [{"text": "European suppliers gain an entry point.", "refs": ["N1"]}],
          "companies": [{"name": "Lockheed Martin", "role": "supplier"}]}


def fake_pages(urls):
    page = {"kind": "html", "status": 200, "markdown": html_to_markdown(PAGE)[1], "title": "PAC-3 contract"}
    return {u: (None if "dead" in u else page) for u in urls}


class FakeCursor:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append(sql)

    def fetchall(self):
        from datetime import datetime
        return [(900, "Wisla phase I signed", "https://arch.example/w", datetime(2018, 3, 28),
                 "In March 2018 Poland signed Wisla phase I.")]


class Scripted:
    """Transport that returns the grouping, research and writer answers in order."""

    def __init__(self, answers):
        self.answers, self.bodies = list(answers), []

    def __call__(self, url, headers, body):
        self.bodies.append(body)
        return self.answers.pop(0)


class PagesTest(unittest.TestCase):
    def test_markdown_drops_scripts_and_nav_and_keeps_anchors(self):
        title, md = html_to_markdown(PAGE)
        self.assertEqual(title, "PAC-3 contract")
        self.assertNotIn("x()", md)
        self.assertNotIn("menu", md)
        secs = sections(md)
        self.assertEqual([s["heading"] for s in secs], ["Award", "Motors"])
        self.assertEqual(secs[0]["anchor"], "s1")

    def test_check_row_year_and_amount(self):
        text = html_to_markdown(PAGE)[1]
        self.assertEqual(check_row({"date": "2026-07-29", "amount": 58620000000}, text), "ok")
        self.assertEqual(check_row({"date": "2019-07-29"}, text), "date_absent")
        self.assertEqual(check_row({"date": "2026-07-29", "amount": 12000000}, text), "amount_absent")
        self.assertIn("58.62", amount_forms(58620000000))

    def test_official_hosts(self):
        self.assertTrue(is_official("https://www.defense.gov/News/x"))
        self.assertTrue(is_official("https://eur-lex.europa.eu/x"))
        self.assertTrue(is_official("https://www.gov.pl/web/obrona"))
        self.assertFalse(is_official("https://www.lockheedmartin.com/x"))
        self.assertFalse(is_official("https://www.defensenews.com/x"))


class LedgerTest(unittest.TestCase):
    def test_tool_tokens_are_charged(self):
        ledger = Ledger()
        cost = ledger.charge(usage(i=1000, o=0, tool=10000))
        self.assertEqual(cost, (11000 * 3 + 10 * 25 + 9) // 10)

    def test_cap_stops_before_call(self):
        ledger = Ledger(cap=10)
        with self.assertRaises(BudgetStop):
            ledger.reserve(1000, 100, grounded=True)

    def test_grounded_body_and_query_count(self):
        ledger = Ledger()
        t = Scripted([reply({"a": 1}, queries=3)])
        out, meta = Gemini("k", ledger, t)("sys", {"p": 1}, {"type": "object"}, grounded=True)
        self.assertEqual(out, {"a": 1})
        self.assertEqual(t.bodies[0]["tools"], [{"google_search": {}}])
        self.assertEqual(ledger.search_queries, 3)
        self.assertEqual(meta["search_queries"], 3)

    def test_text_mode_and_fence(self):
        self.assertEqual(parse_json_text('```json\n{"a": 2}\n```'), {"a": 2})
        body = Gemini("k", Ledger(), None).body("s", {}, {"type": "object"}, True, 100, "text")
        self.assertNotIn("responseJsonSchema", body["generationConfig"])


class RouteTest(unittest.TestCase):
    def run_pilot(self, writer=WRITER, research=RESEARCH):
        groups = {"groups": [{"items": [1, 2], "programme": "PAC-3", "organisations": ["Lockheed Martin"],
                              "terms": ["PAC-3", "Wisla"], "place": {"label": "Poland", "lat": 52.2, "lon": 21.0}}]}
        t = Scripted([reply(groups), reply(research, queries=4, tool=5000), reply(writer)])
        model = Gemini("k", Ledger(), t)
        with tempfile.TemporaryDirectory() as d:
            result = pilot.run(FakeCursor(), model, ITEMS, {1: [{"name": "Lockheed Martin", "company_id": "c1"}]},
                               DOCTRINE, Path(d), fetcher=fake_pages)
            files = sorted(str(p.relative_to(d)) for p in Path(d).rglob("*.md"))
        return result, files, t

    def test_duplicates_merge_and_candidate_passes(self):
        result, files, t = self.run_pilot()
        self.assertEqual(result["groups"], 1)
        ev = result["events"][0]
        self.assertEqual(ev["item_ids"], [11, 12])
        self.assertEqual(ev["outcome"], "CANDIDATE", ev.get("reasons"))
        statuses = {f["id"]: f["status"] for f in ev["facts"]}
        self.assertEqual(statuses["T1"], "ok")
        self.assertEqual(statuses["T3"], "ok")          # archive row checked against our archive text
        self.assertEqual(statuses["T4"], "source_unreachable")
        self.assertEqual(ev["analysis"]["chronology_dates"], 3)
        self.assertEqual(ev["companies_confirmed"][0]["company_id"], "c1")
        official = [d for d in ev["documents"] if d["official"]]
        self.assertTrue(official and official[0]["storage"] == "markdown")
        self.assertTrue(any(f.endswith("full.md") for f in files))
        writer_facts = {f["id"] for f in json.loads(t.bodies[2]["contents"][0]["parts"][0]["text"])["facts"]}
        self.assertNotIn("T4", writer_facts)            # unverified rows never reach the writer

    def test_retelling_is_held(self):
        research = dict(RESEARCH, timeline=RESEARCH["timeline"][:1], supply_chain=[])
        writer = dict(WRITER, policy_direction=[], supply_chain=[])
        result, _, _ = self.run_pilot(writer=writer, research=research)
        ev = result["events"][0]
        self.assertEqual(ev["outcome"], "HOLD")
        self.assertIn("analysis_missing_chronology", ev["reasons"])
        self.assertIn("analysis_missing_vertical_and_doctrine", ev["reasons"])

    def test_invented_number_is_held(self):
        writer = dict(WRITER, context=[{"text": "The US awarded $71 billion in 2026.", "refs": ["T1"]}])
        result, _, _ = self.run_pilot(writer=writer)
        self.assertIn("number_not_in_sources", result["events"][0]["reasons"])

    def test_sentence_without_source_is_held(self):
        writer = dict(WRITER, why_it_matters=[{"text": "It matters.", "refs": []}])
        result, _, _ = self.run_pilot(writer=writer)
        self.assertIn("sentence_without_source", result["events"][0]["reasons"])

    def test_budget_stop_keeps_finished_events(self):
        groups = {"groups": [{"items": [1], "programme": "a", "organisations": [], "terms": [], "place": None},
                             {"items": [2], "programme": "b", "organisations": [], "terms": [], "place": None}]}
        t = Scripted([reply(groups), reply(RESEARCH), reply(WRITER), reply(RESEARCH, i=10**7)])
        model = Gemini("k", Ledger(cap=2_000_000), t)
        with tempfile.TemporaryDirectory() as d:
            result = pilot.run(FakeCursor(), model, ITEMS, {}, DOCTRINE, Path(d), fetcher=fake_pages)
        self.assertEqual(result["stop"], "BUDGET_CAP_REACHED")
        self.assertIn(result["events"][0]["outcome"], ("CANDIDATE", "HOLD"))
        self.assertEqual(result["events"][1]["outcome"], "stopped")

    def test_bad_grouping_falls_back_to_one_item_per_event(self):
        self.assertIsNone(pilot.valid_groups({"groups": [{"items": [1]}]}, 2))


if __name__ == "__main__":
    unittest.main()
