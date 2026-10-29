import json
import urllib.error
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


def note_reply(i=100):
    cand = {"finishReason": "STOP", "content": {"parts": [{"text": "TIMELINE\n2026-07-29 multiyear award."}]},
            "groundingMetadata": {"webSearchQueries": ["q1", "q2"],
                                  "groundingChunks": [{"web": {"uri": "https://www.defense.gov/award", "title": "award"}}],
                                  "groundingSupports": [{"segment": {"text": "2026-07-29 multiyear award."},
                                                         "groundingChunkIndices": [0]}]}}
    return {"candidates": [cand], "usageMetadata": usage(i=i)}


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
        {"date": "2026-07-29", "precision": "day", "what": "Multiyear award for PAC-3 MSE.", "commitment": "ceiling",
         "amount": 58620000000, "currency": "USD", "source_url": "https://www.defense.gov/award"},
        {"date": "2026-08-03", "precision": "day", "what": "Northrop Grumman second source of rocket motors.", "commitment": "framework",
         "source_url": "https://www.defense.gov/award"},
        {"date": "2018-03-01", "precision": "month", "what": "Poland signed Wisla phase I.", "commitment": "contract", "archive_id": 900},
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
        self.assertEqual(check_row({"date": "2026-01-01", "what": "Frigate keel laid at Gdansk shipyard"}, text),
                         "claim_absent")

    def test_ref_marks_are_removed(self):
        out = {"context": [{"text": "Award made [T1, T2].", "refs": ["T1"]}]}
        pilot.strip_refs(out)
        self.assertEqual(out["context"][0]["text"], "Award made.")

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
            ledger.reserve(ledger.worst(1000, 100, True), grounded=True)

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
                              "terms": ["PAC-3", "Wisla"], "countries": ["US", "PL"], "place": {"label": "Poland", "lat": 52.2, "lon": 21.0}}]}
        t = Scripted([reply(groups), note_reply(), reply(research), reply(writer)])
        model = Gemini("k", Ledger(), t)
        with tempfile.TemporaryDirectory() as d:
            result = pilot.run(FakeCursor(), model, ITEMS, {1: [{"name": "Lockheed Martin", "company_id": "c1"}]},
                               DOCTRINE, Path(d), fetcher=fake_pages, template=False)
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
        self.assertNotIn("responseMimeType", t.bodies[1]["generationConfig"])   # search step is free text
        self.assertEqual(t.bodies[1]["tools"], [{"google_search": {}}])
        self.assertIn("[1]", json.loads(t.bodies[2]["contents"][0]["parts"][0]["text"])["note"])
        writer_facts = {f["id"] for f in json.loads(t.bodies[3]["contents"][0]["parts"][0]["text"])["facts"]}
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
        t = Scripted([reply(groups), note_reply(), note_reply(i=10**7)])
        model = Gemini("k", Ledger(cap=2_000_000), t)
        with tempfile.TemporaryDirectory() as d:
            result = pilot.run(FakeCursor(), model, ITEMS, {}, DOCTRINE, Path(d), fetcher=fake_pages)
        self.assertEqual(result["stop"], "BUDGET_CAP_REACHED")
        self.assertEqual([e["outcome"] for e in result["events"]], ["stopped", "stopped"])
        self.assertGreater(model.ledger.steps["research"], 0)

    def test_bad_grouping_falls_back_to_one_item_per_event(self):
        self.assertIsNone(pilot.valid_groups({"groups": [{"items": [1]}]}, 2))


if __name__ == "__main__":
    unittest.main()


class DoctrineTest(unittest.TestCase):
    def test_doctrine_needs_topic_and_country(self):
        recs = [{"jurisdiction": "US", "topics": ["naval-shipbuilding"]}, {"jurisdiction": "GB", "topics": ["naval-shipbuilding"]},
                {"jurisdiction": "EU", "topics": ["naval-shipbuilding"]}]
        self.assertEqual([r["jurisdiction"] for r in pilot.doctrine_for("naval-shipbuilding", recs, ["ES", "SA"])], ["EU"])
        self.assertEqual(pilot.doctrine_for("naval-shipbuilding", recs, ["SA"]), [])


class RobustnessTest(unittest.TestCase):
    def test_trailing_prose_is_ignored(self):
        self.assertEqual(parse_json_text('{"a": 1}\nHope this helps.'), {"a": 1})

    def test_malformed_json_is_retried_once(self):
        bad = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "{a:}"}]}}], "usageMetadata": usage()}
        t = Scripted([bad, reply({"ok": True})])
        out, _ = Gemini("k", Ledger(), t)("s", {}, {"type": "object"})
        self.assertEqual(out, {"ok": True})
        self.assertEqual(len(t.bodies), 2)

    def test_failed_row_is_rescued_by_a_search_page(self):
        row = {"date": "2026-07-29", "what": "Multiyear award for PAC-3 MSE.", "amount": 58620000000}
        page = {"kind": "html", "markdown": html_to_markdown(PAGE)[1], "final_url": "https://real.example/award"}
        status, url, _ = pilot.rescue(row, "source_unreachable", "https://dead.example", {"https://g/1": page})
        self.assertEqual((status, url), ("ok", "https://real.example/award"))

    def test_task_goes_first_in_the_user_turn(self):
        body = Gemini("k", Ledger(), None).body("s", {"p": 1}, {"type": "object"}, True, 100, "text", "medium", "Search now")
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "Search now")
        self.assertEqual(body["generationConfig"]["thinkingConfig"]["thinkingLevel"], "medium")


class RedirectTest(unittest.TestCase):
    def test_redirect_is_replaced_by_final_url(self):
        pages = {"https://vertexaisearch.cloud.google.com/r/1": {"final_url": "https://real.example/a"}}
        self.assertEqual(pilot.real_url("https://vertexaisearch.cloud.google.com/r/1", pages), "https://real.example/a")
        self.assertEqual(pilot.real_url("https://x.example", pages), "https://x.example")


from collectors.kb_pilot.dossier import MemoryStore, programme_key
from collectors.kb_pilot.gemini import find_inlined


def one_group(name="PAC-3 MSE"):
    return {"groups": [{"items": [1, 2], "programme": name, "organisations": ["Lockheed Martin"], "terms": ["PAC-3"],
                        "countries": ["US", "PL"], "topic": "us-munitions-ramp-up",
                        "place": {"label": "Poland", "lat": 52.2, "lon": 21.0}}]}


class DossierTest(unittest.TestCase):
    def run_with(self, store, t):
        model = Gemini("k", Ledger(), t)

        def save(ev, rec, facts, cards):
            return store.save(ev["group"]["programme"], [ev["group"]["programme"]], ev["topic"], None, facts, cards,
                              "run", "v")
        with tempfile.TemporaryDirectory() as d:
            return pilot.run(FakeCursor(), model, ITEMS, {}, DOCTRINE, Path(d), fetcher=fake_pages, store=store,
                             save=save)

    def test_second_run_reuses_the_dossier(self):
        store = MemoryStore()
        first = self.run_with(store, Scripted([reply(one_group()), note_reply(), reply(RESEARCH), reply(WRITER)]))
        added = first["events"][0]["dossier"]["added"]
        self.assertGreaterEqual(added, 3)
        t = Scripted([reply(one_group("PAC-3 MSE")), note_reply(), reply(RESEARCH), reply(WRITER)])
        second = self.run_with(store, t)
        ev = second["events"][0]
        self.assertTrue(ev["known_programme"])
        self.assertEqual(ev["dossier_rows_before"], added)
        self.assertIn("do not repeat them", t.bodies[1]["contents"][0]["parts"][0]["text"])
        self.assertIn("PAC-3 MSE", json.loads(t.bodies[0]["contents"][0]["parts"][0]["text"])["known_programmes"])
        origins = [f["origin"] for f in ev["facts"] if f["kind"] == "T"]
        self.assertIn("dossier", origins)
        self.assertTrue(any(f["status"] == "dossier_repeat" for f in ev["facts"]))
        self.assertEqual(ev["dossier"]["added"], 0)     # nothing new to add

    def test_programme_key(self):
        self.assertEqual(programme_key("PAC-3 MSE (Patriot)"), "pac-3 mse patriot")
        self.assertIsNone(programme_key("!"))


class BatchTest(unittest.TestCase):
    def test_batch_answers_in_order_at_half_price(self):
        created = {"name": "batches/1", "metadata": {"state": "BATCH_STATE_PENDING"}}
        done = {"name": "batches/1", "metadata": {"state": "BATCH_STATE_SUCCEEDED"},
                "response": {"inlinedResponses": {"inlinedResponses": [
                    {"response": reply({"b": 2}), "metadata": {"key": "r1"}},
                    {"response": reply({"a": 1}), "metadata": {"key": "r0"}}]}}}
        polls = [done]
        ledger = Ledger()
        model = Gemini("k", ledger, Scripted([created]), getter=lambda url, h: polls.pop(0), sleep=lambda s: None,
                       batch=True)
        reqs = [model.request("s", {"i": i}, {"type": "object"}, step="writer") for i in range(2)]
        out = model.many(reqs)
        self.assertEqual([o[1] for o in out], [{"a": 1}, {"b": 2}])
        self.assertEqual(ledger.steps["writer"], 2 * int(ledger.cost_of(usage()) * 0.5))
        self.assertEqual(model.batches[0]["state"], "BATCH_STATE_SUCCEEDED")

    def test_failed_batch_stops(self):
        model = Gemini("k", Ledger(), Scripted([{"name": "batches/2", "state": "JOB_STATE_FAILED"}]),
                       sleep=lambda s: None, batch=True)
        reqs = [model.request("s", {}, {"type": "object"}) for _ in range(2)]
        with self.assertRaises(BudgetStop):
            model.many(reqs)

    def test_inlined_paths(self):
        self.assertEqual(find_inlined({"dest": {"inlinedResponses": [1]}}), [1])
        self.assertEqual(find_inlined({"response": {"inlinedResponses": {"inlinedResponses": [2]}}}), [2])


class WriterShapeTest(unittest.TestCase):
    def test_string_sentences_keep_their_refs(self):
        out = pilot.normalize_output({"headline": "h", "lead": "l", "context": ["Award made [T1, T2].", {"text": "x", "refs": ["N1"]}],
                                      "what_happened": "Poland hosts it [N1]."})
        self.assertEqual(out["context"][0]["refs"], ["T1", "T2"])
        self.assertEqual(out["what_happened"][0]["refs"], ["N1"])
        self.assertEqual(out["supply_chain"], [])

    def test_bad_writer_answer_fails_one_event(self):
        t = Scripted([reply(one_group()), note_reply(), reply(RESEARCH), reply(["not", "an", "object"])])
        with tempfile.TemporaryDirectory() as d:
            result = pilot.run(FakeCursor(), Gemini("k", Ledger(), t), ITEMS, {}, DOCTRINE, Path(d), fetcher=fake_pages)
        self.assertEqual(result["events"][0]["outcome"], "error")


class UnwrapTest(unittest.TestCase):
    def test_single_object_in_list(self):
        from collectors.kb_pilot.gemini import unwrap
        self.assertEqual(unwrap([{"a": 1}]), {"a": 1})
        self.assertEqual(unwrap([{"timeline": [1]}, {"timeline": [2], "parent": {}}]), {"timeline": [1, 2], "parent": {}})

    def test_only_batched_requests_put_schema_in_prompt(self):
        m = Gemini("k", Ledger(), None, batch=True)
        r = m.request("s", {}, {"type": "object"})
        self.assertIn("responseJsonSchema", r["body"]["generationConfig"])   # direct call keeps the schema
        t = m.as_text_mode(r)
        self.assertNotIn("responseJsonSchema", t["body"]["generationConfig"])
        self.assertIn("Return only one JSON object", t["body"]["systemInstruction"]["parts"][0]["text"])
        self.assertIn("responseJsonSchema", r["body"]["generationConfig"])   # original untouched


from collectors.kb_pilot.free import FreeModel

SHORT = {"headline": "Poland to host Europe's only PAC-3 centre", "lead": "Europe gets sustainment first.",
         "what_happened": [{"text": "Poland will host the centre.", "refs": ["N1"]}],
         "bigger_picture": [{"text": "Supply becomes transatlantic.", "refs": ["T1", "T2"]}],
         "why_it_matters": [{"text": "European suppliers gain an entry point.", "refs": ["N1"]}],
         "companies": [{"name": "Lockheed Martin", "role": "supplier"}]}


def openai_reply(obj):
    return {"choices": [{"message": {"content": json.dumps(obj)}}], "usage": {"prompt_tokens": 900, "completion_tokens": 300}}


class TemplateTest(unittest.TestCase):
    def test_sections_written_by_code(self):
        facts = [{"id": "T1", "kind": "T", "status": "ok", "row": {"date": "2026-07-29", "precision": "day",
                  "what": "Multiyear award for PAC-3 MSE.", "commitment": "ceiling", "amount": 58620000000, "currency": "USD"}},
                 {"id": "T2", "kind": "T", "status": "source_unreachable", "row": {"date": "2025-01-01", "what": "x"}},
                 {"id": "S1", "kind": "S", "status": "ok", "row": {"upper": "PAC-3 MSE", "lower": "rocket motors", "note": "sole source."}},
                 {"id": "D1", "kind": "D", "status": "ok", "row": DOCTRINE[0]}]
        c = pilot.compose_sections(facts)
        self.assertEqual(c["context"], [{"text": "29 Jul 2026: Multiyear award for PAC-3 MSE (ceiling, USD 58.62 billion).",
                                         "refs": ["T1"]}])
        self.assertEqual(c["supply_chain"][0]["text"], "PAC-3 MSE depends on rocket motors — sole source.")
        self.assertTrue(c["policy_direction"][0]["text"].startswith("US — FY2027 budget"))

    def test_template_run_uses_short_writer_and_free_model(self):
        gem_t = Scripted([note_reply(), reply(RESEARCH)])          # Gemini: only the search step and its fallback
        gem = Gemini("k", Ledger(), gem_t)
        free_t = Scripted([openai_reply(one_group()), openai_reply(RESEARCH), openai_reply(SHORT)])
        cheap = FreeModel(gem, account="a", token="t", transport=free_t)
        with tempfile.TemporaryDirectory() as d:
            result = pilot.run(FakeCursor(), gem, ITEMS, {}, DOCTRINE, Path(d), fetcher=fake_pages, cheap=cheap)
        ev = result["events"][0]
        self.assertEqual(ev["outcome"], "CANDIDATE", ev.get("reasons"))
        self.assertTrue(ev["synthesis"]["context"])                  # composed by code
        self.assertIn("short parts", free_t.bodies[2]["messages"][0]["content"])
        self.assertEqual(result["free"]["free_ok"], 3)
        self.assertNotIn("writer", gem.ledger.steps)                 # Gemini paid only for search

    def test_free_failure_falls_back_to_gemini(self):
        def broken(url, headers, body):
            raise urllib.error.HTTPError(url, 429, "busy", {}, None)
        gem = Gemini("k", Ledger(), Scripted([reply({"a": 1})]))
        cheap = FreeModel(gem, account="a", token="t", transport=broken)
        out, _ = cheap("s", {}, {"type": "object"}, step="structure")
        self.assertEqual(out, {"a": 1})
        self.assertEqual(cheap.stats["fallback"], {"free_http_429": 1})

    def test_without_keys_everything_goes_to_gemini(self):
        gem = Gemini("k", Ledger(), Scripted([reply({"a": 1})]))
        cheap = FreeModel(gem, account="", token="")
        self.assertEqual(cheap("s", {}, {"type": "object"})[0], {"a": 1})
        self.assertEqual(cheap.stats["fallback"], {"free_disabled": 1})
