"""Pure offline tests: never open a socket or use a model key."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from email.message import Message

from collectors.editor.groq_network_probe import (
    READ_CAP, TIMEOUT_SECONDS, URL, USER_AGENT, _NoRedirect, _classify_body,
    _safe_headers, probe,
)


def headers(**kwargs):
    h = Message()
    for k, v in kwargs.items():
        h[k.replace("_", "-")] = v
    return h


class NoAuthResponse:
    status = 200
    def __init__(self, h, body):
        self.headers = h
        self.body = io.BytesIO(body)
        self.bytes_requested = []
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self, amount):
        self.bytes_requested.append(amount)
        return self.body.read(amount)


class TestGroqNetworkProbe(unittest.TestCase):
    def test_fixed_one_get_no_auth_body_model_or_secret(self):
        calls = []
        resp = NoAuthResponse(headers(Content_Type="application/json"), b'{"data":[]}')
        def opener(req, timeout):
            calls.append(req)
            self.assertEqual(req.get_method(), "GET")
            self.assertEqual(req.full_url, URL)
            self.assertEqual(req.data, None)
            self.assertIsNone(req.get_header("Authorization"))
            self.assertEqual(req.get_header("User-agent"), USER_AGENT)
            self.assertEqual(USER_AGENT, "BOHUN-C3-10/1.0")
            self.assertEqual(timeout, TIMEOUT_SECONDS)
            return resp
        out = probe(opener=opener)
        self.assertEqual(len(calls), 1)
        self.assertEqual(resp.bytes_requested, [READ_CAP])
        self.assertFalse(out["authorization_sent"])
        self.assertFalse(out["request_body_sent"])
        self.assertFalse(out["model_inference_requested"])
        self.assertEqual(out["database_reads"], 0)
        self.assertFalse(out["publication_authorized"])
        self.assertNotIn("https://", repr(out))

    def test_401_json_like_is_identifiable(self):
        def reject(req, timeout):
            raise urllib.error.HTTPError(URL, 401, "secret HTTP message",
                headers(Content_Type="application/json", WWW_Authenticate="Bearer", Server="envoy"),
                io.BytesIO(b'{"error":{"message":"SENSITIVE"}}'))
        out = probe(opener=reject)
        self.assertEqual(out["http_status"], 401)
        self.assertEqual(out["body_prefix_class"], "json_like")
        self.assertTrue(out["www_authenticate_present"])
        self.assertNotIn("SENSITIVE", repr(out))
        self.assertNotIn("secret", repr(out))

    def test_403_html_with_cloudflare_only_as_signal(self):
        def reject(req, timeout):
            raise urllib.error.HTTPError(URL, 403, "PRIVATE",
                headers(Content_Type="text/html; charset=utf-8", Server="cloudflare",
                        cf_ray="PRIVATE-RAY-ID", cf_mitigated="challenge"),
                io.BytesIO(b'<html>PRIVATE BLOCK REASON</html>'))
        out = probe(opener=reject)
        self.assertEqual(out["http_status"], 403)
        self.assertEqual(out["content_type_class"], "html")
        self.assertEqual(out["body_prefix_class"], "html_like")
        self.assertEqual(out["server_class"], "cloudflare")
        self.assertTrue(out["cf_ray_present"])
        self.assertTrue(out["cf_mitigated_challenge"])
        self.assertNotIn("PRIVATE", repr(out))
        self.assertNotIn("RAY-ID", repr(out))

    def test_403_html_without_headers_does_not_guess_cloudflare(self):
        def reject(req, timeout):
            raise urllib.error.HTTPError(URL, 403, "private", {}, io.BytesIO(b'<!DOCTYPE html><title>Denied</title>'))
        out = probe(opener=reject)
        self.assertEqual(out["server_class"], "missing")
        self.assertFalse(out["cf_ray_present"])
        self.assertEqual(out["body_prefix_class"], "html_like")

    def test_transport_error_redacts_message(self):
        def reject(req, timeout):
            raise urllib.error.URLError("Private credential no logging")
        out = probe(opener=reject)
        self.assertIsNone(out["http_status"])
        self.assertEqual(out["transport_error"], "network_or_tls_failure")
        self.assertNotIn("Private", repr(out))

    def test_body_cap_enforced_on_benign_200(self):
        resp = NoAuthResponse(headers(Content_Type="text/plain"), b'ABC SECRET ' * 500)
        out = probe(opener=lambda req, timeout: resp)
        self.assertEqual(resp.bytes_requested, [READ_CAP])
        self.assertNotIn("SECRET", repr(out))
        self.assertEqual(out["body_prefix_class"], "other")

    def test_response_headers_are_sanitized(self):
        report = _safe_headers(headers(Server="PRIVATE-TOKEN",
            Content_Type="private/type;password=secret", cf_ray="private-id",
            location="https://secret.example", WWW_Authenticate="Bearer abc"))
        self.assertEqual(report["server_class"], "other")
        self.assertEqual(report["content_type_class"], "other")
        self.assertTrue(report["redirect_location_present"])
        self.assertNotIn("PRIVATE", repr(report))
        self.assertNotIn("secret", repr(report))
        self.assertNotIn("abc", repr(report))

    def test_no_redirect_handler_never_follows(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "move", {}, "https://unexpected.site"))

    def test_json_like_is_prefix_not_attestation(self):
        self.assertEqual(_classify_body(b' {not valid json'), "json_like")
        self.assertEqual(_classify_body(b' <HTML>PRIVATE</HTML>'), "html_like")
        self.assertEqual(_classify_body(b''), "empty")

    def test_report_shape_never_includes_untrusted_text(self):
        resp = NoAuthResponse(headers(Content_Type="text/html", cf_ray="SECRET"), b'<html>secret</html>')
        out = probe(opener=lambda *_, **__: resp)
        self.assertEqual(out["request_count"], 1)
        self.assertFalse(out["source_data_sent"])
        self.assertEqual(out["database_writes"], 0)
        self.assertNotIn("secret", json.dumps(out).lower())
        self.assertEqual(out["schema"], "c3-10-groq-network-v1")


if __name__ == "__main__":
    unittest.main()
