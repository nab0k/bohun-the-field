"""Offline synthetic checks for the capped, read-only external fact-check probe."""
import io
import json
import urllib.error
import unittest
from unittest.mock import patch

from collectors.editor.live_factcheck import (
    MAX_MATERIALS, MAX_OUTPUT_TOKENS, MAX_PACKET_BYTES, MODEL, SQL,
    _date_compatible, _words, call_groq, choose_excerpts, prepare,
    provider_request, query_candidates, run, sanitized_provider_failure, _exit_code,
    _groq_http_request, USER_AGENT,
)


class Column:
    def __init__(self, name): self.name=name


class Cursor:
    description = (Column("id"),Column("event_id"),Column("item_day"))
    def __init__(self, conn): self.conn=conn
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def execute(self, sql, params):
        assert self.conn.readonly
        self.conn.log.append((sql,params))
    def fetchall(self): return self.conn.rows


class Db:
    def __init__(self,rows=(),read_only=True):
        self.rows=rows; self.confirm=read_only; self.readonly=False; self.log=[]
    def execute(self,sql):
        self.log.append((sql,None))
        if sql=="SET TRANSACTION READ ONLY": self.readonly=True;return None
        if sql=="SHOW transaction_read_only":
            flag="on" if self.confirm else "off"
            return type("Rows",(),{"fetchone":lambda s:(flag,)})()
        raise AssertionError("unexpected SQL")
    def cursor(self):return Cursor(self)


PACKET={"request_hash":"abc","claims":[{"claim_id":"c-1","text":"Agency awarded 40 devices."}],
        "spans":[{"source_id":"s-1","span_id":"sp-1","quote":"Agency awarded 40 devices."}]}


def groq_reply(status="supported",check_id="c-1",finish="stop"):
    return {"model":MODEL,"choices":[{"finish_reason":finish,"message":{"content":json.dumps({"checks":[
        {"claim_id":check_id,"status":status,"source_id":"s-1","span_id":"sp-1"}]})}}]}


def synthetic_capture_row():
    """Invented record, never loaded from Supabase or sent to Groq."""
    url = "https://example.org/imaginary-radios"
    return {
        "id": 771, "event_id": 44, "version": 2, "generator": "author|gemini",
        "published_at": "2026-11-09T11:00:00+00:00",
        "headline": "Agency reports procurement of forty coastal radio sets.",
        "what_happened": "The ministry says delivery of forty coastal radio sets is pending acceptance.",
        "sources": [{"url": url, "date": "2026-11-08", "class": "P2"}],
        "context_sources": [], "item_id": 909, "item_url": url,
        "item_day": "2026-11-08", "manifest_day": "2026-11-08",
        "capture_text": (
            "The agency reported procurement of forty coastal radio sets after a tender.\n"
            "The forty coastal radio sets are not yet delivered and acceptance is pending."
        ),
    }


class FactcheckTests(unittest.TestCase):
    def test_real_preparation_path_stays_untrusted_and_within_actual_http_cap(self):
        """Exercise snapshot -> evidence bundle -> checker packet, all offline."""
        from collectors.editor.core import _hash
        from collectors.editor.checker_probe import validate_untrusted_reply
        row = synthetic_capture_row()
        packet, meta = prepare(row)
        self.assertEqual(meta["material_id"], "syn-771")
        self.assertEqual(meta["version"], 2)
        self.assertGreaterEqual(meta["p2_excerpts"], 1)
        self.assertLessEqual(meta["p2_excerpts"], 10)
        self.assertEqual(len(packet["claims"]), meta["claims_selected"])
        self.assertFalse(packet["independent_p1_attested"])
        self.assertFalse(packet["full_material_coverage"] is None)
        self.assertTrue(all(len(span["quote"]) <= 200 and
                            span["quote"] in row["capture_text"]
                            for span in packet["spans"]))
        self.assertTrue(all(span["url"] == row["item_url"] for span in packet["spans"]))
        unhashed = {k:v for k,v in packet.items() if k != "request_hash"}
        self.assertEqual(packet["request_hash"], _hash(unhashed))
        request = provider_request(packet)
        self.assertLessEqual(len(json.dumps(packet, ensure_ascii=False).encode("utf-8")),
                             MAX_PACKET_BYTES)
        self.assertLessEqual(len(json.dumps(request, ensure_ascii=False).encode("utf-8")),
                             MAX_PACKET_BYTES)
        self.assertNotIn("tools", request)
        report = validate_untrusted_reply(packet, {
            "request_hash": packet["request_hash"], "checker_model_id": MODEL,
            "checks": [{"claim_id": claim["claim_id"], "status": "insufficient",
                        "source_id": None, "span_id": None}
                       for claim in packet["claims"]],
        })
        self.assertFalse(report["publication_authorized"])
        self.assertFalse(report["semantic_assessment_trusted"])

    def test_preparation_refuses_bad_manifest_date_before_packet(self):
        row = synthetic_capture_row()
        row["manifest_day"] = "2026-11-07"
        with self.assertRaisesRegex(ValueError, "manifest_capture_date_mismatch"):
            prepare(row)

    def test_preparation_refuses_same_family_checker_as_author(self):
        row = synthetic_capture_row()
        row["generator"] = "openai/gpt-oss-20b"
        with self.assertRaisesRegex(ValueError, "non_gemini_author_skipped"):
            prepare(row)

    def test_tokenization_unicode(self):
        self.assertIn("ракета",_words("Ракета запущена 40 раз."))

    def test_relevance_selection_uses_matching_passage(self):
        body="Miscellaneous news about unrelated story.\nAgency awarded forty devices for the program."
        quotes=choose_excerpts(body,[{"text":"Agency awarded forty devices."}])
        self.assertEqual(len(quotes),1)
        self.assertIn("Agency awarded",quotes[0])

    def test_no_fake_excerpt_when_no_overlap(self):
        body="Unrelated generic report about economics, private offices and staff management."
        with self.assertRaisesRegex(ValueError,"no_lexically_related_excerpt"):
            choose_excerpts(body,[{"text":"Boreal launch 938 aircraft."}])

    def test_long_capture_refused(self):
        with self.assertRaisesRegex(ValueError,"invalid_capture_length"):
            choose_excerpts("a"*15001,[{"text":"any"}])

    def test_only_exact_manifest_date(self):
        self.assertTrue(_date_compatible({"manifest_day":"2026-11-07","item_day":"2026-11-07"}))
        self.assertFalse(_date_compatible({"manifest_day":"","item_day":"2026-11-07"}))
        self.assertFalse(_date_compatible({"manifest_day":"2026-11-06","item_day":"2026-11-07"}))
        self.assertFalse(_date_compatible({"manifest_day":"2026-02-31","item_day":"2026-02-31"}))

    def test_query_enforces_server_read_only_first(self):
        db=Db([(1,2,"2026-11-07")])
        self.assertEqual(query_candidates(db,hours=24)[0]["id"],1)
        self.assertEqual(db.log[0][0],"SET TRANSACTION READ ONLY")
        self.assertEqual(db.log[1][0],"SHOW transaction_read_only")
        self.assertEqual(db.log[2][1],(24,))

    def test_deny_when_read_only_unconfirmed(self):
        db=Db(read_only=False)
        with self.assertRaisesRegex(RuntimeError,"database_read_only_unconfirmed"):
            query_candidates(db)
        self.assertEqual(len(db.log),2)

    def test_hours_bounds(self):
        for bad in (0,169,True):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): query_candidates(Db(),hours=bad)

    def test_fixed_sql_not_writer(self):
        up=SQL.upper()
        for keyword in ("UPDATE ","DELETE ","INSERT ","SYNTHESIS_ADMISSION"):
            self.assertNotIn(keyword,up)
        self.assertIn("EVENT_DOCUMENT",up)
        self.assertIn("CONTENT_CAPTURE",up)

    def test_groq_request_sets_identifiable_user_agent(self):
        req = _groq_http_request(b'{"model":"test"}', "fake-test-only")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.get_header("User-agent"), USER_AGENT)
        self.assertEqual(USER_AGENT, "BOHUN-C3-10/1.0")
        self.assertEqual(req.get_header("Authorization"), "Bearer fake-test-only")

    def test_payload_no_browser_or_tools(self):
        req=provider_request(PACKET)
        self.assertEqual(req["model"],MODEL)
        self.assertEqual(req["max_completion_tokens"],MAX_OUTPUT_TOKENS)
        self.assertNotIn("tools",req)
        self.assertNotIn("tool_choice",req)

    def test_provider_missing_key_fails(self):
        with self.assertRaisesRegex(ValueError,"groq_key_absent"):
            call_groq(provider_request(PACKET),"")

    def test_provider_fake_http(self):
        def transport(raw,key):
            self.assertEqual(key,"fake-key")
            req=json.loads(raw)
            self.assertEqual(req["model"],MODEL)
            return groq_reply()
        self.assertEqual(call_groq(provider_request(PACKET),"fake-key",transport)["checks"][0]["status"],"supported")

    def test_provider_truncated_fails(self):
        with self.assertRaisesRegex(ValueError,"provider_output_incomplete"):
            call_groq(provider_request(PACKET),"fake",lambda *_:groq_reply(finish="length"))

    def test_provider_mismatched_model_fails(self):
        answer=groq_reply();answer["model"]="gemini"
        with self.assertRaisesRegex(ValueError,"provider_model_identity_mismatch"):
            call_groq(provider_request(PACKET),"fake",lambda *_:answer)

    def test_run_requires_key_before_db(self):
        db=Db()
        with self.assertRaisesRegex(ValueError,"groq_key_absent"):
            run(db,"")
        self.assertFalse(db.log)

    def test_run_limit_bounds(self):
        with self.assertRaisesRegex(ValueError,"invalid_limit"):
            run(Db(),"fake",limit=MAX_MATERIALS+1)

    def test_workflow_marks_provider_error_as_failed_after_report(self):
        self.assertEqual(_exit_code({"results":[{"status":"ERROR"}]}),2)

    def test_workflow_keeps_nonerror_status_success(self):
        self.assertEqual(_exit_code({"results":[{"status":"PROVISIONAL"}]}),0)
        self.assertEqual(_exit_code({"results":[{"status":"SKIP"}]}),0)

    def test_http_error_code_does_not_leak_remote_sensitive_text(self):
        e=urllib.error.HTTPError("https://example.test/private-secret",401,
                                 "Bearer token confidential",None,
                                 io.BytesIO(b"secret response body"))
        code=sanitized_provider_failure(e)
        self.assertEqual(code,"provider_http_401")
        self.assertNotIn("secret",code)
        self.assertNotIn("token",code)

    def test_rate_limit_remains_distinguishable(self):
        e=urllib.error.HTTPError("https://example.test",429,
                                 "customer-private-detail",None,None)
        self.assertEqual(sanitized_provider_failure(e),"provider_http_429")

    def test_unknown_http_code_is_closed(self):
        e=urllib.error.HTTPError("https://example.test",499,
                                 "private",None,None)
        self.assertEqual(sanitized_provider_failure(e),"provider_http_other")

    def test_provider_finish_reason_is_safe_specific_code(self):
        self.assertEqual(sanitized_provider_failure(
            ValueError("provider_output_incomplete")),"provider_output_incomplete")

    def test_unlisted_value_error_never_leaks_model_text(self):
        self.assertEqual(sanitized_provider_failure(
            ValueError("model output contains private story")),
            "provider_reply_validation_failed")

    def test_json_decoder_error_has_closed_code(self):
        error=json.JSONDecodeError("private", "{ not-json", 1)
        self.assertEqual(sanitized_provider_failure(error),"provider_reply_invalid_json")

    def test_url_error_and_timeout_are_safe(self):
        self.assertEqual(sanitized_provider_failure(
            urllib.error.URLError("private source")),"provider_transport_error")
        self.assertEqual(sanitized_provider_failure(
            TimeoutError("private network description")),"provider_connection_error")

    @patch("collectors.editor.live_factcheck.prepare")
    def test_run_http_error_yields_safe_reason_and_no_retry(self,prep):
        db=Db([(1,2,"2026-11-07")])
        prep.return_value=(PACKET,{"material_id":"syn-1","version":1})
        attempts=[]
        def denied(raw,key):
            attempts.append(1)
            raise urllib.error.HTTPError("https://secret.example",400,
                                         "private quote and key",None,None)
        out=run(db,"fake",transport=denied)
        self.assertEqual(len(attempts),1)
        self.assertEqual(out["results"][0]["reason_code"],"provider_http_400")
        self.assertNotIn("private",repr(out))
        self.assertFalse(out["publication_authorized"])

    @patch("collectors.editor.live_factcheck.prepare")
    def test_run_provider_error_fails_closed_without_retry(self,prep):
        db=Db([(1,2,"2026-11-07")])
        prep.return_value=(PACKET,{"material_id":"syn-1","version":1})
        calls=[]
        def transport(raw,key):
            calls.append(1)
            raise TimeoutError("contains sensitive text secret")
        out=run(db,"fake",transport=transport)
        self.assertEqual(out["attempted_calls"],1)
        self.assertEqual(len(calls),1)
        self.assertEqual(out["results"][0]["status"],"ERROR")
        self.assertNotIn("secret",str(out))
        self.assertFalse(out["publication_authorized"])

    @patch("collectors.editor.live_factcheck.prepare")
    def test_run_skips_unlinked_without_network(self,prep):
        db=Db([(1,2,"2026-11-07")])
        prep.side_effect=ValueError("manifest_capture_date_mismatch")
        out=run(db,"fake",transport=lambda *_:self.fail("network call"))
        self.assertEqual(out["attempted_calls"],0)
        self.assertEqual(out["results"][0]["status"],"SKIP")


if __name__=="__main__":unittest.main()
