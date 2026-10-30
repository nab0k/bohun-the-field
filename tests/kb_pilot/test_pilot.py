import io
import json
import urllib.error
from datetime import datetime
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
        self.assertIn("Return only the JSON object itself", t["body"]["systemInstruction"]["parts"][0]["text"])
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
        self.assertEqual(cheap.stats["fallback"], {"cloudflare_http_429": 1, "paid_gemini": 1})

    def test_without_keys_everything_goes_to_gemini(self):
        gem = Gemini("k", Ledger(), Scripted([reply({"a": 1})]))
        cheap = FreeModel(gem, account="", token="")
        self.assertEqual(cheap("s", {}, {"type": "object"})[0], {"a": 1})
        self.assertEqual(cheap.stats["fallback"], {"free_disabled": 1, "paid_gemini": 1})


class ChunkTest(unittest.TestCase):
    def test_batches_are_sent_in_chunks(self):
        import collectors.kb_pilot.gemini as g
        sent = []
        model = Gemini("k", Ledger(), None, batch=True)
        model._batch = lambda reqs: sent.append(len(reqs)) or [("ok", {}, {})] * len(reqs)
        reqs = [model.request("s", {}, {"type": "object"}) for _ in range(g.BATCH_CHUNK * 2 + 3)]
        self.assertEqual(len(model.many(reqs)), len(reqs))
        self.assertEqual(sent, [g.BATCH_CHUNK, g.BATCH_CHUNK, 3])


class IncompleteTest(unittest.TestCase):
    def test_empty_free_writer_falls_back(self):
        from collectors.kb_pilot.prompts import SHORT_WRITER_SCHEMA
        gem = Gemini("k", Ledger(), Scripted([reply({"headline": "h", "lead": "l", "what_happened": [{"text": "x", "refs": ["N1"]}],
                                                     "bigger_picture": [], "why_it_matters": [], "companies": []})]))
        empty = {"headline": "", "lead": "", "what_happened": [], "bigger_picture": [], "why_it_matters": [], "companies": []}
        cheap = FreeModel(gem, account="a", token="t", transport=Scripted([openai_reply(empty)]))
        out, _ = cheap("s", {}, SHORT_WRITER_SCHEMA, step="writer")
        self.assertEqual(out["headline"], "h")
        self.assertEqual(cheap.stats["fallback"], {"cloudflare_incomplete": 1, "paid_gemini": 1})

    def test_admission_holds_empty_writer(self):
        facts = [{"id": "T%d" % i, "kind": "T", "status": "ok", "row": {"date": "2026-0%d-01" % i}} for i in range(1, 4)]
        facts.append({"id": "S1", "kind": "S", "status": "ok", "row": {}})
        decision, reasons, _ = pilot.admit(facts, {"headline": "", "supply_chain": [{"text": "x", "refs": ["S1"]}]}, [])
        self.assertEqual(decision, "HOLD")
        self.assertIn("writer_incomplete", reasons)


class RouterTest(unittest.TestCase):
    def test_providers_in_order_and_off_on_403(self):
        from collectors.kb_pilot.free import OpenAICompatible
        calls = []

        def transport(url, headers, body):
            calls.append(url)
            if "groq" in url:
                raise urllib.error.HTTPError(url, 403, "forbidden", {}, None)
            return openai_reply({"a": 1})
        gem = Gemini("k", Ledger(), None)
        provs = [OpenAICompatible("groq", "https://groq/x", "k", "m"), OpenAICompatible("mistral", "https://mistral/x", "k", "m")]
        cheap = FreeModel(gem, transport=transport, providers=provs)
        for _ in range(2):
            self.assertEqual(cheap("s", {}, {"type": "object", "required": ["a"]})[0], {"a": 1})
        self.assertEqual(calls, ["https://groq/x", "https://mistral/x", "https://mistral/x"])   # groq off after 403
        self.assertEqual(cheap.stats["by_provider"], {"mistral": 2})

    def test_schema_echo_is_unwrapped(self):
        from collectors.kb_pilot.free import check_answer
        schema = {"required": ["headline", "what_happened"]}
        self.assertEqual(check_answer({"properties": {"headline": "h", "what_happened": [1]}}, schema)["headline"], "h")

    def test_writer_packet_is_capped(self):
        facts = [{"id": "N1", "kind": "N", "status": "news", "text": "n", "row": None}]
        facts += [{"id": "T%d" % i, "kind": "T", "status": "ok", "text": "t", "row": {"date": "2020-01-%02d" % (i % 28 + 1)}}
                  for i in range(1, 140)]
        packet = pilot.short_writer_packet([ITEMS[0]], facts, {"context": [{"text": "x", "refs": ["T1"]}]})
        self.assertLessEqual(len(packet["facts"]), pilot.MAX_WRITER_FACTS)


class EmbedTest(unittest.TestCase):
    def test_origin_prefers_earliest_same_text_then_near_duplicate(self):
        from collectors.kb_pilot.embed import origin_of
        from datetime import datetime
        it = {"item_id": 5, "text_sha256": "a", "published": datetime(2026, 10, 28)}
        nb = [{"item_id": 3, "text_sha256": "b", "origin_item_id": None, "published": datetime(2026, 10, 27), "distance": 0.03},
              {"item_id": 2, "text_sha256": "a", "origin_item_id": None, "published": datetime(2026, 10, 27), "distance": 0.0}]
        self.assertEqual(origin_of(it, nb), (2, "same_text"))
        self.assertEqual(origin_of(it, nb[:1]), (3, "near_duplicate"))
        far = [dict(nb[0], distance=0.3)]
        self.assertEqual(origin_of(it, far), (5, "self"))

    def test_origin_same_second_copies_root_at_lower_id(self):
        from collectors.kb_pilot.embed import origin_of
        from datetime import datetime
        t = datetime(2026, 10, 28, 9, 0)
        a = {"item_id": 10, "text_sha256": "x", "published": t}
        b = {"item_id": 11, "text_sha256": "x", "published": t}
        nb_a = [{"item_id": 11, "text_sha256": "x", "origin_item_id": None, "published": t, "distance": 0.0}]
        nb_b = [{"item_id": 10, "text_sha256": "x", "origin_item_id": 10, "published": t, "distance": 0.0}]
        self.assertEqual(origin_of(a, nb_a), (10, "self"))
        self.assertEqual(origin_of(b, nb_b), (10, "same_text"))

    def test_embed_response_shape_is_checked(self):
        from collectors.kb_pilot.embed import embed_texts
        ok = lambda req: {"result": {"shape": [1, 1024], "data": [[0.0] * 1024]}}
        self.assertEqual(len(embed_texts(["x"], "a", "t", ok)[0]), 1024)
        with self.assertRaises(RuntimeError):
            embed_texts(["x", "y"], "a", "t", ok)


class EmbedFallbackTest(unittest.TestCase):
    def test_cloudflare_refusal_switches_to_local(self):
        from collectors.kb_pilot import embed

        class Cur:
            def __init__(self, rows): self.rows, self.sql = rows, []
            def execute(self, q, p=None): self.sql.append(q)
            def fetchall(self):
                q = self.sql[-1]
                return self.rows if "LEFT JOIN knowledge.item_embedding" in q else []
            def __enter__(self): return self
            def __exit__(self, *a): return False

        class Conn:
            def __init__(self): self.cur = Cur([(1, "t", "b", datetime(2026, 10, 29))])
            def cursor(self): return self.cur
            def commit(self): pass

        def refuse(req):
            raise urllib.error.HTTPError("u", 429, "limit", {}, io.BytesIO(b'{"errors":[{"message":"daily"}]}'))
        stats = embed.run(Conn(), "a", "t", transport=refuse, local=lambda texts: [[0.0] * 1024 for _ in texts])
        self.assertEqual(stats["by_backend"], {embed.LOCAL_MODEL: 1})
        self.assertEqual(stats["error_codes"]["429"], ["daily"])


class MarketModelTest(unittest.TestCase):
    def test_node_key_matches_transfer_sql(self):
        from collectors.kb_pilot.dossier import node_key
        self.assertEqual(node_key(" MTU and Wärtsilä "), "mtu and w rtsil")
        self.assertEqual(node_key("Укроборонпром"), "x-" + __import__("hashlib").md5("Укроборонпром".encode()).hexdigest()[:10])

    def test_edge_ends_by_row_kind(self):
        from collectors.kb_pilot.dossier import edge_ends
        self.assertEqual(edge_ends("supply_chain", {"upper": "Navantia", "lower": "Titanium", "level": "raw_material"}),
                         (("material", "Titanium"), ("company", "Navantia")))
        self.assertEqual(edge_ends("supply_chain", {"upper": "", "lower": "MTU", "level": "component"}),
                         (("company", "MTU"), None))
        self.assertEqual(edge_ends("restriction", {"issuer": "U.S. DoS", "kind": "export_licensing", "subject": ""}),
                         (("regulation", "U.S. DoS export_licensing"), None))
        self.assertEqual(edge_ends("chronology", {"date": "2026-01-01"}), (None, None))

    def test_db_store_writes_edges_in_programme_context(self):
        from collectors.kb_pilot.dossier import DbStore

        class Cur:
            def __init__(self):
                self.calls, self.next_id, self.rowcount, self.last = [], 100, 1, None

            def execute(self, sql, args=()):
                self.calls.append((" ".join(sql.split()), args))
                self.last = sql

            def fetchone(self):
                if "SELECT id, key" in self.last:
                    return None
                self.next_id += 1
                return (self.next_id,)

        cur = Cur()
        facts = [{"kind": "S", "status": "ok", "text": "MTU supplies engines", "source_url": "https://a.example/x",
                  "row": {"upper": "Navantia", "lower": "MTU", "level": "component"}},
                 {"kind": "T", "status": "ok", "text": "Contract", "source_url": "https://a.example/y",
                  "row": {"date": "2026-10-01", "precision": "day", "commitment": "suspension"}}]
        out = DbStore(cur).save("Avante 2200", ["Avante 2200"], "naval", None, facts, [], "run", "v18")
        edges = [a for s, a in cur.calls if s.startswith("INSERT INTO knowledge.edge")]
        self.assertEqual(out, {"programme": 101, "added": 2})
        supply, milestone = edges
        self.assertEqual(supply[:4], (102, "supplies", 103, 101))   # MTU → Navantia in the programme
        self.assertEqual(milestone[:5], (101, "milestone", None, 101, "negative"))
        self.assertFalse(any("dossier_entry" in s or "knowledge.programme " in s for s, _ in cur.calls))


class ExtractTest(unittest.TestCase):
    TEXT = ("Leonardo selects Shield AI for rotary-wing autonomy\n\nLeonardo has selected Shield AI’s Hivemind to "
            "develop autonomy for the Proteus demonstrator. The UK Ministry of Defence awarded a £60 million contract "
            "in 2022. The programme was delayed by a year.")
    ITEM = {"item_id": 7, "url": "https://x.example/a", "published": "2026-10-29T08:00:00+00:00", "origin": 7}

    def answer(self, **rel):
        base = {"src": "Shield AI", "rel": "supplies", "dst": "Proteus",
                "quote": "Leonardo has selected Shield AI's Hivemind to develop autonomy for the Proteus demonstrator"}
        return {"entities": [{"name": "Shield AI", "kind": "company"}, {"name": "Proteus", "kind": "programme"},
                             {"name": "UK Ministry of Defence", "kind": "org"}, {"name": "other", "kind": "programme"}],
                "relations": [dict(base, **rel)]}

    def test_quote_must_be_in_the_text(self):
        from collectors.kb_pilot.extract import checked
        ents, kept, dropped = checked(self.answer(), self.TEXT)  # curly apostrophe in the text, straight in the quote
        self.assertEqual(len(kept), 1)
        _, kept, dropped = checked(self.answer(quote="Leonardo picked Shield AI for the Proteus demonstrator now"),
                                   self.TEXT)
        self.assertEqual((kept, dict(dropped)), ([], {"quote_not_in_text": 1}))

    def test_amount_unknown_end_and_generic_names_are_dropped(self):
        from collectors.kb_pilot.extract import checked
        q = "The UK Ministry of Defence awarded a £60 million contract in 2022"
        _, kept, _ = checked(self.answer(src="UK Ministry of Defence", rel="orders", dst="Proteus", amount=60000000,
                                         currency="GBP", quote=q), self.TEXT)
        self.assertEqual(len(kept), 1)
        for rel, reason in ((dict(amount=75000000, src="UK Ministry of Defence", rel="orders", quote=q),
                             "amount_not_in_quote"),
                            (dict(dst="Leonardo"), "unknown_end"),
                            (dict(dst="other"), "generic_name")):
            _, kept, dropped = checked(self.answer(**rel), self.TEXT)
            self.assertEqual(dict(dropped), {reason: 1}, rel)

    def test_edge_row_dates_hash_and_context(self):
        from collectors.kb_pilot.extract import checked, context_of, edge_row
        ents, kept, _ = checked(self.answer(rel="milestone", dst="", negative=True, src="Proteus",
                                            quote="The programme was delayed by a year"), self.TEXT)
        row = edge_row(kept[0], ents, self.ITEM)
        self.assertEqual((row["rel"], row["dst"], row["occurred_on"], row["polarity"]),
                         ("milestone", None, None, "negative"))  # undated fact: never the article date
        again = edge_row(kept[0], ents, dict(self.ITEM, item_id=8))  # a reprint with the same first source
        self.assertEqual(row["row_hash"], again["row_hash"])
        self.assertEqual(context_of(ents, [row]), ("programme", "Proteus"))

    def test_generic_programme_is_not_saved_to_the_dossier(self):
        store = MemoryStore()
        self.assertEqual(store.save("Other", ["Other"], "x", None, [], [], "r", "v"), {"programme": None, "added": 0})

    def test_run_skips_reprints_and_stops_without_paid_fallback(self):
        from collectors.kb_pilot import extract

        class Cur:
            def __init__(self, rows):
                self.rows, self.calls, self.rowcount = rows, [], 1

            def execute(self, sql, args=()):
                self.calls.append((" ".join(sql.split())[:40], args))

            def fetchall(self):
                return self.rows

            def fetchone(self):
                return None if self.calls[-1][0].startswith("SELECT id, status") else (len(self.calls),)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        from datetime import datetime, timezone
        t = datetime(2026, 10, 29, tzinfo=timezone.utc)
        rows = [(1, "A", "u1", self.TEXT, t, 1, "self"), (2, "A", "u2", self.TEXT, t, 1, "same_text")]
        cur = Cur(rows)

        class Conn:
            def cursor(self):
                return cur

            def commit(self):
                pass

        class Cheap:
            enabled = True

            def request(self, system, packet, schema, **k):
                return {"step": k.get("step")}

            providers, off = [], set()

            def free_many(self, reqs, exclude=None):
                if reqs and reqs[0]["step"] == "verify":
                    assert exclude == [{"gemini_free"}] * len(reqs)
                    return [("ok", {"verdicts": [{"n": 0, "ok": True}]}, {"model": "groq"}) for _ in reqs]
                return [("ok", ExtractTest.answer(ExtractTest()), {"model": "gemini_free"}) for _ in reqs]

        stats = extract.run(Conn(), Cheap(), "2026-10-29", "2026-10-29")
        self.assertEqual((stats["items"], stats["reprints"], stats["done"], stats["kept"], stats["verified"]),
                         (2, 1, 1, 1, 1))
        logs = [a for s, a in cur.calls if s.startswith("INSERT INTO knowledge.item_extraction")]
        self.assertEqual(sorted((a[0], a[2]) for a in logs), [(1, "done"), (2, "skipped_reprint")])

        class Spent(Cheap):
            enabled = False
        cur.calls.clear()
        self.assertEqual(extract.run(Conn(), Spent(), "2026-10-29", "2026-10-29")["stop"], "FREE_PROVIDERS_SPENT")


class ExtractV2Test(unittest.TestCase):
    TEXT = ("Interceptors\n\nThe three-year first phase established the interceptor concept in August 2025. "
            "GM Defense was awarded a $697.7 million contract modification for the ISV-U.")
    ENTS = [{"name": "HYDEF", "kind": "programme"}, {"name": "GM Defense", "kind": "company"},
            {"name": "ISV-U", "kind": "product"}, {"name": "US Army", "kind": "org"}]

    def check(self, **rel):
        from collectors.kb_pilot.extract import checked
        return checked({"entities": self.ENTS, "relations": [rel]}, self.TEXT, "2026-10-29T08:00:00+00:00")

    def test_date_must_match_the_year_in_the_quote(self):
        q = "The three-year first phase established the interceptor concept in August 2025"
        _, kept, dropped = self.check(src="HYDEF", rel="milestone", date="2026-10-29", quote=q)
        self.assertEqual(dict(dropped), {"date_not_in_quote": 1})
        _, kept, _ = self.check(src="HYDEF", rel="milestone", date="2025-08-01", precision="month", quote=q)
        self.assertEqual(len(kept), 1)

    def test_amount_scale_and_undated_year_from_quote(self):
        from collectors.kb_pilot.extract import edge_row
        q = "GM Defense was awarded a $697.7 million contract modification for the ISV-U"
        _, _, dropped = self.check(src="GM Defense", rel="supplies", dst="ISV-U", amount=697.7, quote=q)
        self.assertEqual(dict(dropped), {"amount_scale": 1})
        ents, kept, _ = self.check(src="GM Defense", rel="supplies", dst="ISV-U", amount=697700000, quote=q)
        self.assertEqual(len(kept), 1)
        ents, kept, _ = self.check(src="HYDEF", rel="milestone",
                                   quote="The three-year first phase established the interceptor concept in August 2025")
        row = edge_row(kept[0], ents, {"published": "2026-10-29T08:00:00+00:00", "origin": 1})
        self.assertEqual((row["occurred_on"], row["date_precision"]), ("2025-01-01", "year"))

    def test_verdicts_keep_only_agreed_rows(self):
        from collectors.kb_pilot.extract import apply_verdicts, verify_packet
        rows = [{"src": ("company", "A"), "rel": "orders", "dst": ("product", "B"), "occurred_on": None, "amount": None,
                 "currency": None, "quantity": None, "quote": "q%d" % n} for n in range(3)]
        self.assertEqual([r["n"] for r in verify_packet(rows)["relations"]], [0, 1, 2])
        kept, rejected, silent = apply_verdicts(rows, {"verdicts": [{"n": 0, "ok": True}, {"n": 1, "ok": False}]})
        self.assertEqual((len(kept), rejected, silent), (1, 1, 1))

    def test_free_many_excludes_the_extracting_provider(self):
        from collectors.kb_pilot.free import FreeModel

        class P:
            def __init__(self, name):
                self.name = name

            def call(self, req, transport):
                return {"verdicts": [{"n": 0, "ok": True}]}, {}

        class Fallback:
            ledger, batches = Ledger(), []

        fm = FreeModel(Fallback(), providers=[P("gemini_free"), P("groq")])
        out = fm.free_many([{"schema": {"required": ["verdicts"]}, "step": "verify"}], exclude=[{"gemini_free"}])
        self.assertEqual(out[0][2]["model"], "groq")

    def test_weak_judges_only_when_nothing_stronger_is_left(self):
        from collectors.kb_pilot.extract import verifier_excludes

        class P:
            def __init__(self, name):
                self.name = name

        class Cheap:
            providers = [P("gemini_free"), P("cloudflare"), P("mistral"), P("groq")]
            off = set()
        self.assertEqual(verifier_excludes(Cheap, "gemini_free"), {"gemini_free", "cloudflare", "mistral"})
        Cheap.off = {"groq"}
        self.assertEqual(verifier_excludes(Cheap, "gemini_free"), {"gemini_free"})


class PublishTest(unittest.TestCase):
    REC = {"titles": ["Leonardo selects Shield AI"], "item_ids": [11, 12], "type": None, "topic": "uncrewed-systems",
           "date": "2026-10-29", "primary_source": {"url": "https://shield.ai/news/x", "title": "Shield AI"},
           "news": [{"url": "https://a.example/1", "outlet": "A", "date": "2026-10-29"},
                    {"url": "https://shield.ai/news/x", "outlet": "Shield", "date": "2026-10-29"}],
           "synthesis": {"headline": "Leonardo picks Shield AI Hivemind to give the Proteus uncrewed rotorcraft its "
                                     "autonomy brain for trials",
                         "what_happened": [{"text": "Leonardo selected Shield AI.", "refs": ["N1"]}],
                         "context": [{"text": "2024-01 — Proteus starts.", "refs": ["T1"]},
                                     {"text": "2026-10 — Hivemind picked.", "refs": ["T2"]}],
                         "policy_direction": [], "bigger_picture": [], "supply_chain": [],
                         "why_it_matters": [{"text": "Autonomy goes rotary.", "refs": ["N1"]}]}}

    def test_version_and_sources_fit_the_feed_tables(self):
        from collectors.kb_pilot.publish import sources_of, version_of
        v = version_of(self.REC)
        self.assertLessEqual(len(v["headline"]), 90)
        self.assertTrue(v["headline"].endswith("…"))
        self.assertEqual(v["context"], "2024-01 — Proteus starts.\n2026-10 — Hivemind picked.")
        self.assertIsNone(v["supply_chain"])
        src = sources_of(self.REC)
        self.assertEqual([s["role"] for s in src], ["primary", "report"])  # the primary is not repeated

    def test_publish_hold_writes_a_hidden_held_synthesis(self):
        from collectors.kb_pilot.publish import publish_hold

        class Cur:
            def __init__(self):
                self.calls, self.n = [], 0

            def execute(self, sql, args=()):
                self.calls.append((" ".join(sql.split()), args))

            def fetchone(self):
                self.n += 1
                return (100 + self.n,)

        cur = Cur()
        self.assertEqual(publish_hold(cur, self.REC, "run"), 102)
        adm = [a for s, a in cur.calls if s.startswith("INSERT INTO knowledge.synthesis_admission")][0]
        self.assertEqual(adm, (102, ["owner_review"]))
        sql = [s for s, _ in cur.calls if s.startswith("INSERT INTO knowledge.synthesis_admission")][0]
        self.assertIn("'HOLD'", sql)
        self.assertIn("false)", sql)
        docs = [a for s, a in cur.calls if s.startswith("INSERT INTO knowledge.event_document")]
        self.assertEqual(docs, [(101, 11), (101, 12)])
        with self.assertRaises(ValueError):
            publish_hold(Cur(), dict(self.REC, synthesis=dict(self.REC["synthesis"], what_happened=[])), "run")


class GroupChunkTest(unittest.TestCase):
    def test_large_windows_are_grouped_in_chunks_with_global_numbers(self):
        calls = []

        def model(system, packet, schema, **k):
            n = len(packet["items"])
            calls.append(n)
            if len(calls) == 2:
                raise ValueError("MODEL_INCOMPLETE_OUTPUT")
            return {"groups": [{"items": list(range(1, n + 1)), "programme": "X"}]}, {"cost_micro_usd": 5}
        items = [{"title": "t%d" % i, "outlet": "o", "published": "2026-10-29T00:00:00", "text": "x"} for i in range(90)]
        groups, meta = pilot.group_items(model, items)
        self.assertEqual(calls, [40, 40, 10])
        self.assertEqual(sorted(n for g in groups for n in g["items"]), list(range(1, 91)))
        self.assertEqual(groups[0]["items"], list(range(1, 41)))
        self.assertEqual(len(groups), 1 + 40 + 1)  # the failed chunk falls back to one group per item
        self.assertEqual((meta["cost_micro_usd"], meta["fallback"]), (10, "invalid_grouping"))


class AdmitTest(unittest.TestCase):
    V = {"headline": "Leonardo picks Shield AI Hivemind for Proteus",
         "what_happened": "Leonardo selected Shield AI to integrate Hivemind into Proteus. The deal is worth $50 million.",
         "context": "2024-01 — Proteus starts.", "policy_direction": None, "supply_chain": None,
         "bigger_picture": "Autonomy moves to rotary platforms.", "why_it_matters": "Suppliers of autonomy gain a "
         "new prime customer.", "sources": [{"url": "https://a.example"}]}
    SRC = "Leonardo has selected Shield AI to integrate its Hivemind software into the Proteus demonstrator."

    class Cheap:
        providers, off = [], set()

        def __init__(self, answers):
            self.answers = list(answers)

        def request(self, *a, **k):
            return {}

        def free_many(self, reqs, exclude=None):
            return [self.answers.pop(0) for _ in reqs]

    def test_unsupported_number_is_fixed_or_dropped_then_published(self):
        from collectors.kb_pilot import admit
        items = admit.review_items(self.V)
        self.assertEqual([sec for _, sec, _ in items],
                         ["headline", "what_happened", "what_happened", "bigger_picture", "why_it_matters"])
        first = {"defence_relevant": True, "verdicts": [{"n": 0, "ok": True}, {"n": 1, "ok": True},
                              {"n": 2, "ok": False, "why": "no amount", "fix": "The value was not disclosed in the source."},
                              {"n": 3, "ok": True}, {"n": 4, "ok": True}]}
        second = {"verdicts": [{"n": 2, "ok": True}]}
        cheap = self.Cheap([("ok", first, {"model": "groq"}), ("ok", second, {"model": "groq"})])
        texts, provider, st = admit.review(cheap, self.SRC, self.V, items)
        self.assertEqual((provider, st["fixed"], st["dropped"]), ("groq", 1, 0))
        new = admit.rebuild(self.V, items, texts)
        self.assertNotIn("$50 million", new["what_happened"])
        self.assertEqual(admit.decide(new, items, texts, False, 0), ("PUBLISH", []))

    def test_fix_with_a_new_number_is_dropped_and_rules_hold(self):
        from collectors.kb_pilot import admit
        items = admit.review_items(self.V)
        first = {"verdicts": [{"n": 0, "ok": False, "fix": "Leonardo pays $70 million for Shield AI autonomy"},
                              {"n": 1, "ok": True}, {"n": 2, "ok": False}, {"n": 3, "ok": True}, {"n": 4, "ok": False}]}
        texts, _, st = admit.review(self.Cheap([("ok", first, {"model": "groq"})]), self.SRC, self.V, items)
        self.assertEqual(st["dropped"], 3)
        new = admit.rebuild(self.V, items, texts)
        decision, reasons = admit.decide(new, items, texts, True, 0)
        self.assertEqual(decision, "HOLD")
        self.assertEqual(set(reasons), {"headline_unsupported", "why_it_matters_empty", "too_many_unsupported",
                                        "story_already_in_feed"})

    def test_no_reviewer_means_no_decision_and_limit_holds(self):
        from collectors.kb_pilot import admit
        items = admit.review_items(self.V)
        self.assertIsNone(admit.review(self.Cheap([("error", RuntimeError("no_free_provider"))]), self.SRC, self.V,
                                       items))
        texts = {n: t for n, _, t in items}
        self.assertEqual(admit.decide(dict(self.V), items, texts, False, 10, 10), ("HOLD", ["run_limit"]))


    def test_off_topic_brief_is_held(self):
        from collectors.kb_pilot import admit
        items = admit.review_items(AdmitTest.V)
        first = {"defence_relevant": False, "relevance_why": "fintech",
                 "verdicts": [{"n": n, "ok": True} for n, _, _ in items]}
        texts, _, st = admit.review(AdmitTest.Cheap([("ok", first, {"model": "groq"})]), AdmitTest.SRC, AdmitTest.V,
                                    items)
        self.assertFalse(st["relevant"])
        new = admit.rebuild(AdmitTest.V, items, texts)
        self.assertEqual(admit.decide(new, items, texts, False, 0, relevant=st["relevant"]),
                         ("HOLD", ["not_defence_relevant"]))


    def test_redirect_sources_are_removed_and_held_text_is_not_stored(self):
        from collectors.kb_pilot import admit
        v = dict(AdmitTest.V, sources=[{"url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/x"},
                                       {"url": "https://a.example/news"}])
        items = admit.review_items(v)
        texts = {n: t for n, _, t in items}
        new = admit.rebuild(v, items, texts)
        self.assertEqual(new["sources"], [{"url": "https://a.example/news"}])
        only_redirect = admit.rebuild(dict(v, sources=v["sources"][:1]), items, texts)
        self.assertEqual(admit.decide(only_redirect, items, texts, False, 0)[1], ["no_real_source"])

        class Cur:
            calls = []

            def execute(self, sql, args=()):
                self.calls.append(" ".join(sql.split())[:40])
        row = {"synthesis_id": 1, "version": 1, "v": v}
        admit.write(Cur(), row, dict(new, what_happened=None), "HOLD", ["what_happened_empty"], "groq", "r")
        self.assertEqual(Cur.calls, ["UPDATE knowledge.synthesis_admission SET"])


class EnoughKnowledgeTest(unittest.TestCase):
    def test_extracted_edges_become_dossier_rows(self):
        from collectors.kb_pilot.dossier import edge_as_row
        self.assertEqual(edge_as_row("orders", "2026-10-29", "day", 269000000, "USD", "contract", "TOTE selected Hanwha",
                                     "US Navy", "LSM", None, "positive")[1]["what"], "US Navy orders LSM: TOTE selected Hanwha")
        self.assertIsNone(edge_as_row("milestone", None, None, None, None, None, "x", "P", None, None, "positive"))
        kind, row = edge_as_row("supplies", None, None, None, None, None, "q", "MTU", "Navantia", "component", "positive")
        self.assertEqual((kind, row["upper"], row["lower"]), ("supply_chain", "Navantia", "MTU"))
        self.assertTrue(edge_as_row("milestone", "2026-01-01", "year", None, None, None, "delay", "P", None, None,
                                    "negative")[1]["commitment"].startswith("cancelled/delayed"))

    def test_rule(self):
        rows = [{"kind": "chronology"}] * 5 + [{"kind": "supply_chain"}]
        self.assertTrue(pilot.enough_knowledge(rows))
        self.assertFalse(pilot.enough_knowledge(rows[:4] + rows[5:]))
        self.assertFalse(pilot.enough_knowledge(rows[:5]))

    def test_known_node_skips_search_and_structure(self):
        dossier = [{"kind": "chronology", "row": {"date": "2020-0%d-01" % m, "precision": "month", "what": "step %d" % m,
                                                  "commitment": "contract"}, "source_url": "https://s.example/%d" % m,
                    "archive_id": None, "anchor": None} for m in range(1, 7)]
        dossier.append({"kind": "supply_chain", "row": {"upper": "Lockheed Martin", "lower": "Aerojet", "level":
                        "component", "note": "motors"}, "source_url": "https://s.example/sc", "archive_id": None})

        class Store(MemoryStore):
            def find_node(self, names):
                return {"id": 7, "name": "PAC-3 MSE", "key": "pac-3 mse", "aliases": [], "edges": 7}

            def load_node(self, node_id):
                return dossier
        model = Gemini("k", Ledger(), Scripted([reply(one_group()), reply(WRITER)]))
        with tempfile.TemporaryDirectory() as d:
            res = pilot.run(FakeCursor(), model, ITEMS, {}, DOCTRINE, Path(d), fetcher=fake_pages, store=Store())
        ev = res["events"][0]
        self.assertTrue(ev["enough_knowledge"])
        self.assertEqual(ev["known_node"], "PAC-3 MSE")
        self.assertEqual(ev["search_queries"], 0)
        self.assertNotEqual(ev["outcome"], "error")


class ValidGroupsTest(unittest.TestCase):
    def test_malformed_answers_fall_back_instead_of_crashing(self):
        for answer in ({"groups": ["1,2", {"items": [3]}]}, {"groups": [{"items": ["1", 2]}]}, {"groups": "x"},
                       ["not", "a", "dict"], None, {"groups": [{"items": [1]}, {"items": [True]}]}):
            self.assertIsNone(pilot.valid_groups(answer, 2), answer)
        self.assertEqual(pilot.valid_groups({"groups": [{"items": [2, 1]}]}, 2), [{"items": [2, 1]}])

    def test_prepare_tolerates_odd_field_types(self):
        group = {"items": [1], "programme": 5, "organisations": "Saab", "terms": [None, "radar"], "countries": None}
        ev = pilot.prepare(None, group, [dict(ITEMS[0], topic=None)], DOCTRINE["records"] if isinstance(DOCTRINE, dict)
                           else DOCTRINE, MemoryStore())
        self.assertEqual(ev["terms"], ["Saab", "radar"])
        self.assertIsNone(group["programme"])


class DailyChainTest(unittest.TestCase):
    class Cur:
        def __init__(self, columns=None, fn=True):
            self.calls, self.columns, self.fn, self.last = [], columns, fn, None

        def execute(self, sql, args=()):
            self.calls.append((" ".join(sql.split())[:50], args))
            self.last = (sql, args)

        def fetchall(self):
            table = self.last[1][1]
            return [(c,) for c in (self.columns or {}).get(table, [])]

        def fetchone(self):
            return (self.fn,)

    def test_only_finished_events_are_marked(self):
        cur = self.Cur()
        events = [{"outcome": "CANDIDATE", "item_ids": [1, 2], "cost_micro_usd": 50},
                  {"outcome": "HOLD", "item_ids": [3], "reasons": ["no_chronology"]},
                  {"outcome": "error", "item_ids": [4]}, {"outcome": "stopped", "item_ids": [5]}]
        self.assertEqual(pilot.mark_processed(cur, events), 3)
        marks = [a for s, a in cur.calls]
        self.assertEqual([(a[0], a[2], a[4]) for a in marks], [(1, "event", 50), (2, "event", 0), (3, "hold", 0)])

    def test_contract_reports_missing_columns_and_functions(self):
        from collectors.kb_pilot import contract
        full = {name.split(".")[1]: cols for name, cols in contract.COLUMNS.items()}
        self.assertEqual(contract.missing(self.Cur(full)), [])
        broken = dict(full, items=["id", "title", "url", "published_at"])
        self.assertEqual(contract.missing(self.Cur(broken, fn=False)),
                         ["intelligence.items.raw_data", "public.export_synthesis_feed()",
                          "public.export_synthesis_feed_v2()"])


class FindTest(unittest.TestCase):
    def test_generic_and_suspect_programmes_are_not_found(self):
        from collectors.kb_pilot.dossier import DbStore

        class Cur:
            sql = []

            def execute(self, sql, args=()):
                self.sql.append(" ".join(sql.split()))

            def fetchone(self):
                return None
        store = DbStore(Cur())
        self.assertIsNone(store.find("Other"))
        self.assertEqual(Cur.sql, [])  # a generic name never reaches the database
        store.find("PAC-3 MSE")
        self.assertIn("status <> 'suspect'", Cur.sql[-1])


class CapAndJudgeTest(unittest.TestCase):
    def test_batch_chunk_shrinks_to_the_remaining_cap(self):
        g = Gemini("k", Ledger(cap=10**9), lambda *a: None, batch=True)
        reqs = [g.request("s", {"x": "y" * 2000}, {"type": "object"}, grounded=True, max_output=10000, step="research")
                for _ in range(20)]
        self.assertEqual(g.chunk_size(reqs), 20)
        one = g.ledger.worst(len(__import__("json").dumps(reqs[0]["body"])), 10000, True) // 2
        g.ledger.cap = int(one * 3.5)
        self.assertIn(g.chunk_size(reqs), (2, 3))
        g.ledger.cap = 1
        self.assertEqual(g.chunk_size(reqs), 1)

    def test_weak_judge_does_not_decide(self):
        from collectors.kb_pilot import admit
        items = admit.review_items(AdmitTest.V)
        ok = {"defence_relevant": True, "verdicts": [{"n": n, "ok": True} for n, _, _ in items]}
        cheap = AdmitTest.Cheap([("ok", ok, {"model": "cloudflare"})])
        self.assertIsNone(admit.review(cheap, AdmitTest.SRC, AdmitTest.V, items))


class PlaceTest(unittest.TestCase):
    def test_place_is_validated(self):
        from collectors.kb_pilot.publish import place_of
        self.assertEqual(place_of({"place": {"label": "Kyiv", "lat": 50.45, "lon": 30.52}}), ("Kyiv", 50.45, 30.52))
        for bad in ({"place": {"lat": 0, "lon": 0}}, {"place": {"lat": 120, "lon": 1}}, {"place": "Kyiv"}, {},
                    {"place": {"lat": "50", "lon": 30}}, {"place": {"lat": True, "lon": 30}}):
            self.assertEqual(place_of(bad), (None, None, None), bad)

    def test_publish_writes_the_place(self):
        from collectors.kb_pilot.publish import publish_hold

        class Cur:
            calls, n = [], 0

            def execute(self, sql, args=()):
                self.calls.append((" ".join(sql.split())[:40], args))

            def fetchone(self):
                Cur.n += 1
                return (Cur.n,)
        cur = Cur()
        publish_hold(cur, dict(PublishTest.REC, place={"label": "Rome", "lat": 41.9, "lon": 12.5}), "r")
        event = [a for s, a in cur.calls if s.startswith("INSERT INTO knowledge.event (")][0]
        self.assertEqual(event[-3:], ("Rome", 41.9, 12.5))
