import contextlib
from datetime import date, datetime, timezone
import json
import unittest

from collectors.synthesis import live
from collectors.synthesis.contracts import canonical, sha
from collectors.synthesis.live import BudgetStop, GeminiWriter, Ledger, micro_usd


class FakeCursor:
    """Records SQL; answers the few SELECTs the live stages issue; RETURNING id yields 1, 2, 3..."""
    def __init__(self, items=(), doctrine=(), processed=0):
        self.items, self.doctrine, self.processed = list(items), list(doctrine), processed
        self.sql, self.next_id, self.result = [], 0, []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))
        if "FROM intelligence.items" in sql:
            self.result = self.items
        elif "FROM knowledge.doctrine d" in sql:
            self.result = self.doctrine
        elif "count(*) FROM knowledge.item_processing" in sql:
            self.result = [(self.processed,)]
        elif "RETURNING id" in sql:
            self.next_id += 1; self.result = [(self.next_id,)]
        else:
            self.result = []

    def fetchall(self): return self.result
    def fetchone(self): return self.result[0] if self.result else None


class FakeConn:
    def __init__(self, cursor): self.cur = cursor
    def cursor(self): return self.cur
    @contextlib.contextmanager
    def transaction(self): yield


def reply(text, prompt=1000, out=200):
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": text}]}}],
            "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": out}}


class LedgerTests(unittest.TestCase):
    def test_price_matches_pipeline_costing(self):
        self.assertEqual(micro_usd(1_000_000, 0), 300_000)
        self.assertEqual(micro_usd(0, 1_000_000), 2_500_000)

    def test_reserve_blocks_before_cap(self):
        ledger = Ledger(cap=10_000, spent=9_000)
        with self.assertRaises(BudgetStop):
            ledger.reserve(5_000)

    def test_missing_usage_is_unknown_cost(self):
        with self.assertRaises(BudgetStop):
            Ledger().charge(None)

    def test_thoughts_are_charged(self):
        ledger = Ledger()
        ledger.charge({"promptTokenCount": 10, "candidatesTokenCount": 10, "thoughtsTokenCount": 10})
        self.assertEqual(ledger.spent, micro_usd(10, 20))


class WriterTests(unittest.TestCase):
    def req(self):
        return {"system_instruction": "x", "schema": {"type": "object"}, "packet": {"facts": []},
                "temperature": .2, "packet_hash": "h"}

    def test_absent_key_stops(self):
        with self.assertRaises(BudgetStop):
            GeminiWriter("", Ledger())

    def test_transport_error_stops_without_retry(self):
        calls = []
        def boom(*a):
            calls.append(1); raise OSError("net")
        w = GeminiWriter("k", Ledger(), boom)
        with self.assertRaises(BudgetStop):
            w(self.req())
        self.assertEqual(len(calls), 1)

    def test_json_reply_and_cost(self):
        ledger = Ledger()
        w = GeminiWriter("k", ledger, lambda url, h, b: reply('{"ok": 1}'))
        self.assertEqual(w(self.req()), {"ok": 1})
        self.assertEqual(ledger.calls, 1)
        self.assertGreater(ledger.spent, 0)

    def test_response_schema_is_sent_without_length_keywords(self):
        seen = {}
        def capture(url, headers, body):
            seen.update(body); return reply("{}")
        req = self.req(); req["schema"] = {"type": "object", "properties": {"a": {"type": "string", "maxLength": 9}}}
        GeminiWriter("k", Ledger(), capture)(req)
        sent = seen["generationConfig"]["responseJsonSchema"]
        self.assertEqual(sent, {"type": "object", "properties": {"a": {"type": "string"}}})
        self.assertEqual(seen["generationConfig"]["thinkingConfig"], {"thinkingLevel": "low"})

    def test_key_goes_in_header_not_url(self):
        seen = {}
        def capture(url, headers, body):
            seen.update(url=url, headers=headers); return reply("{}")
        GeminiWriter("secret-k", Ledger(), capture)(self.req())
        self.assertNotIn("secret-k", seen["url"])
        self.assertEqual(seen["headers"]["x-goog-api-key"], "secret-k")


def event(decision="PUBLISH", codes=(), writer=True, why=True, cls="P1", modality="signed", hits=None):
    fact = {"id": "Fabc", "document_id": "doc1", "predicate": "contract_award", "modality": modality,
            "modality_hits": hits or {}, "subject_name": "Aster Labs", "object_text": "radar",
            "amounts": [{"value": "20000000", "currency": "USD"}], "commitment": "contract",
            "quote": "Aster Labs signed a contract.", "quote_sha256": sha("q"), "quote_verified": True,
            "artifact_sha256": sha("a"), "block": "what_happened", "text": "Aster Labs signed a contract."}
    out = {"headline": {"text": "Aster Labs wins radar contract", "fact_ids": ["Fabc"]},
           "what_happened": [{"text": "Aster Labs signed a contract.", "fact_ids": ["Fabc"]}],
           "context": [], "policy_direction": [], "bigger_picture": [], "supply_chain": [],
           "why_it_matters": [{"text": "It covers radar.", "fact_ids": ["Fabc"]}] if why else []}
    claims = [{"section": "headline", "claim_text": out["headline"]["text"], "check_status": "ok", "check_codes": []},
              {"section": "what_happened", "claim_text": "Aster Labs signed a contract.", "check_status": "ok", "check_codes": []}]
    return {"document_ids": ["doc1"], "type": "contract", "title": "Aster Labs wins", "procedure_ref": None,
            "topic": {"main": {"slug": "sensors"}, "all": [{"slug": "sensors"}]},
            "packet": {"event": {"first_published_at": "2026-10-19T12:00:00+00:00"}, "facts": [fact],
                       "sources": [{"document_id": "doc1", "url": "https://example.org/a", "outlet": "X",
                                    "date": "2026-10-19", "role": "report", "class": cls}]},
            "writer_output": out if writer else None, "text_checks": {"claims": claims} if writer else None,
            "admission": {"decision": decision, "reason_codes": list(codes), "rules_version": "synthesis-v2.1"}}


class WriteEventTests(unittest.TestCase):
    def sql(self, cur, table):
        return [p for s, p in cur.sql if s.startswith("INSERT INTO knowledge." + table + " ")]

    def test_publish_is_held_for_owner_review_and_never_public(self):
        cur = FakeCursor()
        event_id, outcome, codes = live.write_event(cur, event(), {"doc1": 7}, "v")
        self.assertEqual((outcome, codes), ("hold", ["owner_review"]))
        admission = [s for s, p in cur.sql if "synthesis_admission" in s][0]
        self.assertIn("'auto-admission',false", admission)
        self.assertEqual(self.sql(cur, "synthesis_admission")[0][1], "HOLD")

    def test_reject_writes_nothing(self):
        cur = FakeCursor()
        self.assertEqual(live.write_event(cur, event("REJECT", ["off_topic"]), {"doc1": 7}, "v")[:2], (None, "reject"))
        self.assertEqual(cur.sql, [])

    def test_unknown_source_class_writes_nothing(self):
        cur = FakeCursor()
        live.write_event(cur, event("HOLD", ["source_unassessed"], cls="unknown"), {"doc1": 7}, "v")
        self.assertEqual(cur.sql, [])

    def test_empty_why_is_stored_empty_and_held(self):
        cur = FakeCursor()
        _, outcome, codes = live.write_event(cur, event("HOLD", ["why_unsupported"], why=False), {"doc1": 7}, "v")
        self.assertEqual((outcome, codes), ("hold", ["why_unsupported"]))
        self.assertEqual(self.sql(cur, "synthesis_version")[0][7], "")
        self.assertEqual(self.sql(cur, "synthesis_admission")[0][1], "HOLD")

    def test_broken_reply_shape_writes_no_version(self):
        cur = FakeCursor(); e = event("HOLD", ["text_mismatch"]); e["writer_output"] = {"headline": "x"}
        live.write_event(cur, e, {"doc1": 7}, "v")
        self.assertEqual(self.sql(cur, "synthesis_version"), [])

    def test_negation_stored_with_prefix(self):
        cur = FakeCursor()
        live.write_event(cur, event("HOLD", ["text_mismatch"], modality="negated", hits={"negated": ["drops"]}), {"doc1": 7}, "v")
        params = self.sql(cur, "fact")[0]
        self.assertEqual((params[2], params[7]), ("not_contract_award", "announced"))

    def test_negation_without_marker_fails(self):
        with self.assertRaises(ValueError):
            live.write_event(FakeCursor(), event(modality="negated"), {"doc1": 7}, "v")

    def test_claims_link_only_known_facts(self):
        cur = FakeCursor()
        live.write_event(cur, event(), {"doc1": 7}, "v")
        self.assertEqual(len(self.sql(cur, "synthesis_claim")), 3)
        self.assertEqual(len(self.sql(cur, "synthesis_claim_fact")), 3)


class StageTests(unittest.TestCase):
    def test_already_processed_stops_before_model(self):
        cur = FakeCursor(processed=3)
        calls = []
        out = live.stage_control_live(FakeConn(cur), None, "k", lambda *a: calls.append(1))
        self.assertEqual(out["stop"], "ALREADY_PROCESSED")
        self.assertEqual(calls, [])

    def test_doctrine_snapshot_shape(self):
        cur = FakeCursor(doctrine=[(3, "sensors", "US", "Plan", date(2024, 4, 8), "More radars.", "https://x.gov/a")])
        rows = live.doctrine_snapshot(cur)["policy_direction"]
        self.assertEqual(rows[0]["id"], "D3")
        self.assertEqual(rows[0]["quote"], rows[0]["source_text"])


class DayLiveTests(unittest.TestCase):
    def test_window_is_bounded(self):
        self.assertEqual(live.window_bounds("2026-10-27", "2026-10-28"), ("2026-10-27", "2026-10-29"))
        for bad in (("2026-10-28", "2026-10-27"), ("2026-10-20", "2026-10-28")):
            with self.assertRaises(SystemExit):
                live.window_bounds(*bad)

    def test_window_loader_builds_documents(self):
        raw = {"origin_title": "Outlet", "content_capture": {"text": "Body text " * 40}}
        items = [(5, "ext1", "Title", "https://example.org/a", datetime(2026, 10, 27, tzinfo=timezone.utc), raw),
                 (6, "ext2", "Title", "http://insecure.example/a", datetime(2026, 10, 27, tzinfo=timezone.utc), raw)]
        docs, ids, report = live.load_window(FakeCursor(items=items), "2026-10-27", "2026-10-28")
        self.assertEqual(ids, {"ext1": 5}); self.assertEqual(report, {"found": 1, "skipped": 1})
        self.assertTrue(docs[0].text.startswith("Title\n"))

    def test_empty_window_stops_before_model(self):
        calls = []
        out = live.stage_day_live(FakeConn(FakeCursor()), None, "k", "2026-10-27", "2026-10-28", lambda *a: calls.append(1))
        self.assertEqual(out["stop"], "NO_UNPROCESSED_ITEMS_WITH_BODY"); self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
