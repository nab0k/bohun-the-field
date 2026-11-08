"""Offline synthetic checks for the capped, read-only external fact-check probe."""
import json
import unittest
from unittest.mock import patch

from collectors.editor.live_factcheck import (
    MAX_MATERIALS, MAX_OUTPUT_TOKENS, MODEL, SQL,
    _date_compatible, _words, call_groq, choose_excerpts,
    provider_request, query_candidates, run,
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


class FactcheckTests(unittest.TestCase):
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
