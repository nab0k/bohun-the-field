"""No-credential synthetic tests for the one-call access probe."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error

from collectors.editor.groq_access_probe import (
    ENDPOINT, MAX_COMPLETION_TOKENS, MODEL, USER_AGENT, _safe_code, benign_payload,
    report, request_once,
)


class Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *_): return False


class ProbeTests(unittest.TestCase):
    def test_payload_has_no_captured_or_project_material(self):
        p=benign_payload()
        s=json.dumps(p)
        self.assertEqual(p["model"], MODEL)
        self.assertEqual(p["max_completion_tokens"],MAX_COMPLETION_TOKENS)
        self.assertNotIn("synthesis",s.lower())
        self.assertNotIn("bohun",s.lower())
        self.assertNotIn("https://",s)

    def test_success_one_post(self):
        calls=[]
        def opener(req,timeout):
            calls.append(req)
            self.assertEqual(req.get_method(),"POST")
            self.assertEqual(req.full_url, ENDPOINT)
            self.assertEqual(req.get_header("User-agent"), USER_AGENT)
            self.assertEqual(USER_AGENT, "BOHUN-C3-10/1.0")
            self.assertEqual(timeout,25)
            self.assertEqual(json.loads(req.data)["model"],MODEL)
            return Response()
        r=report("test-token",opener=opener)
        self.assertEqual(len(calls),1)
        self.assertTrue(r["request_accepted"])
        self.assertFalse(r["publication_authorized"])
        self.assertFalse(r["user_material_sent"])
        self.assertEqual(r["attempted_provider_calls"],1)
        self.assertEqual(r["database_reads"],0)
        self.assertEqual(r["database_writes"],0)
        self.assertNotIn("test-token",str(r))

    def test_org_block_safe_error_code(self):
        def deny(req,timeout):
            body=b'{"error":{"code":"model_permission_blocked_org","type":"permissions_error","message":"SECRET PRIVATE MESSAGE"}}'
            raise urllib.error.HTTPError(ENDPOINT,403,"SECRET",{},io.BytesIO(body))
        r=report("test-secret",opener=deny)
        self.assertEqual(r["http_status"],403)
        self.assertEqual(r["error_code"],"model_permission_blocked_org")
        self.assertEqual(r["error_type"],"permissions_error")
        self.assertNotIn("SECRET",str(r))
        self.assertFalse(r["request_accepted"])

    def test_project_block_safe_error_code(self):
        def deny(req,timeout):
            body=b'{"error":{"code":"model_permission_blocked_project","type":"permissions_error"}}'
            raise urllib.error.HTTPError(ENDPOINT,403,"SECRET",{},io.BytesIO(body))
        self.assertEqual(report("key",opener=deny)["error_code"],"model_permission_blocked_project")

    def test_unknown_error_fields_never_leak(self):
        code, typ = _safe_code(b'{"error":{"code":"SENSITIVE_CLIENT_ID","type":"TOPSECRET","message":"SECRET TEXT"}}')
        self.assertEqual((code,typ),("other","other"))

    def test_html_403_is_not_printed(self):
        code, typ = _safe_code(b'<html>secret response</html>')
        self.assertEqual((code,typ),("unreadable","unreadable"))

    def test_no_key_no_call(self):
        calls=[]
        with self.assertRaisesRegex(ValueError,"missing_groq_key"):
            request_once("",opener=lambda *args: calls.append(args))
        self.assertEqual(calls,[])

    def test_transport_failure_no_message(self):
        def fail(req,timeout):
            raise urllib.error.URLError("SECRET Internal address")
        r=report("test-key",opener=fail)
        self.assertEqual(r["error_code"],"transport_failure")
        self.assertNotIn("SECRET",str(r))

    def test_rate_limit_code(self):
        def deny(req,timeout):
            raise urllib.error.HTTPError(ENDPOINT,429,"SECRET",{},io.BytesIO(b'{"error":{"code":"rate_limit_exceeded","type":"rate_limit_error"}}'))
        r=report("token",opener=deny)
        self.assertEqual(r["http_status"],429)
        self.assertEqual(r["error_code"],"rate_limit_exceeded")

    def test_unknown_http_code(self):
        def deny(req,timeout):
            raise urllib.error.HTTPError(ENDPOINT,403,"secret",{},io.BytesIO(b'{}'))
        r=report("test",opener=deny)
        self.assertEqual(r["http_status"],403)
        self.assertEqual(r["error_code"],"other")
        self.assertNotIn("secret",str(r))


if __name__ == "__main__":
    unittest.main()
