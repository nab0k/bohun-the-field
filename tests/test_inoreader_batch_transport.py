"""HTTP boundary tests use fake responses/clock; one short real signal guard test."""
from contextlib import nullcontext
import io
import hashlib
import json
import socket
import sqlite3
import ssl
import subprocess
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

with (patch("socket.socket", side_effect=AssertionError("IMPORT_NETWORK")),
      patch("socket.getaddrinfo", side_effect=AssertionError("IMPORT_DNS")),
      patch("urllib.request.urlopen", side_effect=AssertionError("IMPORT_HTTP")),
      patch("sqlite3.connect", side_effect=AssertionError("IMPORT_DB")),
      patch("subprocess.Popen", side_effect=AssertionError("IMPORT_PROCESS"))):
    from collectors.inoreader import batch_transport as transport
    from collectors.inoreader.batch_delivery import BatchError, CAPS


class Clock:
    def __init__(self):
        self.now = 0.0
    def __call__(self):
        return self.now


class Response(io.BytesIO):
    def __init__(self, body=b'{}', *, length=True, status=200, on_read=None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))} if length else {}
        self.status, self.on_read = status, on_read
        self.read_sizes = []
    def getcode(self):
        return self.status
    def read(self, n=-1):
        self.read_sizes.append(n)
        if self.on_read:
            self.on_read()
        return super().read(n)


class Opener:
    def __init__(self, response, on_open=None):
        self.response, self.on_open, self.calls = response, on_open, []
    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if self.on_open:
            self.on_open()
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class TransportTests(unittest.TestCase):
    def setUp(self):
        for name in ("socket.socket", "socket.getaddrinfo", "urllib.request.urlopen", "sqlite3.connect", "subprocess.Popen"):
            guard = patch(name, side_effect=AssertionError("UNEXPECTED_EXTERNAL_IO"))
            guard.start()
            self.addCleanup(guard.stop)

    def reader(self, response, caps=None, on_open=None, provider=None):
        clock = Clock()
        budget = transport.Budget(dict(CAPS) | (caps or {}), clock=clock)
        opener = Opener(response, on_open)
        client = transport.Reader(provider or (lambda: "SYNTHETIC_TOKEN"), budget, opener=opener,
                                  deadline=lambda _: nullcontext())
        return client, budget, opener, clock

    def test_exact_byte_caps_and_native_id_encoded_in_fixed_read_only_path(self):
        response = Response(b'{"items":[]}')
        c, budget, opener, _ = self.reader(response, {"response_bytes": 12, "total_bytes": 12})
        self.assertEqual(c.stream("feed/https://example.test/feed?a=b"), {"items": []})
        request, timeout = opener.calls[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertTrue(request.full_url.startswith(c.API+"stream/contents/feed%2Fhttps%3A%2F%2F"))
        self.assertTrue(request.full_url.endswith("?n=1&output=json"))
        self.assertLessEqual(timeout, 10)
        self.assertTrue(response.closed)
        self.assertEqual(budget.bytes, 12)
        self.assertEqual(c.receipts[0]["body_sha256"], hashlib.sha256(b'{"items":[]}').hexdigest())
        self.assertTrue(c.receipts[0]["body_complete"])
        self.assertNotIn("SYNTHETIC_TOKEN", json.dumps(c.receipts))
        with self.assertRaisesRegex(BatchError, "BYTE_BUDGET"):
            c.subscriptions()
        self.assertEqual(len(opener.calls), 1)

    def test_header_over_cap_never_reads_body_and_does_not_refund_attempt(self):
        r = Response(b'{}')
        r.headers["Content-Length"] = "101"
        c, b, opener, _ = self.reader(r, {"response_bytes": 100, "requests": 1})
        with self.assertRaisesRegex(BatchError, "RESPONSE_LIMIT"):
            c.subscriptions()
        self.assertEqual(r.read_sizes, [])
        self.assertEqual(b.requests, 1)
        self.assertTrue(r.closed)
        with self.assertRaisesRegex(BatchError, "REQUEST_BUDGET"):
            c.subscriptions()
        self.assertEqual(len(opener.calls), 1)

    def test_unknown_length_at_limit_fails_without_cap_plus_one_read(self):
        r = Response(b'{}', length=False)
        c, b, _, _ = self.reader(r, {"response_bytes": 2})
        with self.assertRaisesRegex(BatchError, "RESPONSE_LIMIT"):
            c.subscriptions()
        self.assertEqual(r.read_sizes, [2])
        self.assertEqual(b.bytes, 2)
        self.assertFalse(c.receipts[0]["body_complete"])
        c, _, _, _ = self.reader(Response(b'{}', length=False), {"response_bytes": 3})
        self.assertEqual(c.subscriptions(), {})

    def test_truncated_invalid_length_utf8_duplicate_json_and_status_are_fixed_failures(self):
        truncated = Response(b'{}')
        truncated.headers["Content-Length"] = "4"
        invalid_length = Response()
        invalid_length.headers["Content-Length"] = "garbage"
        for r in (truncated, invalid_length, Response(b'\xff'), Response(b'{"a":1,"a":2}'), Response(status=403)):
            c, _, _, _ = self.reader(r)
            with self.assertRaises(BatchError):
                c.subscriptions()
            self.assertTrue(r.closed)

    def test_429_has_no_retry_and_closes_error_body(self):
        body = io.BytesIO(b"PROVIDER_PRIVATE_ERROR")
        err = urllib.error.HTTPError("https://example.test/PRIVATE", 429, "PRIVATE", {}, body)
        c, b, opener, _ = self.reader(err)
        with self.assertRaisesRegex(BatchError, "^HTTP_STATUS$"):
            c.subscriptions()
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(b.requests, 1)
        self.assertTrue(body.closed)

    def test_redirect_is_closed_without_following_or_forwarding_auth(self):
        body = io.BytesIO(b"PRIVATE")
        with self.assertRaisesRegex(BatchError, "AUTH_REDIRECT_FORBIDDEN"):
            transport.NoRedirect().redirect_request(None, body, 302, "PRIVATE", {}, "https://other.test/")
        self.assertTrue(body.closed)

    def test_dns_header_and_body_time_budget_and_clock_regression_stop(self):
        for stage in ("open", "body"):
            c, b, opener, clock = self.reader(Response(), {"seconds": 1})
            advance = lambda: setattr(clock, "now", 2)
            if stage == "open":
                opener.on_open = advance
            else:
                opener.response.on_read = advance
            with self.assertRaisesRegex(BatchError, "TIME_BUDGET"):
                c.subscriptions()
            self.assertEqual(b.requests, 1)
            self.assertTrue(opener.response.closed)
        c, b, opener, clock = self.reader(Response())
        clock.now = -1
        with self.assertRaisesRegex(BatchError, "TIME_BUDGET"):
            c.subscriptions()
        self.assertEqual(opener.calls, [])

    def test_credential_failure_is_redacted_before_network(self):
        def bad_token():
            raise RuntimeError("SECRET_SENTINEL")
        c, b, opener, _ = self.reader(Response(), provider=bad_token)
        with self.assertRaisesRegex(BatchError, "^HTTP_READ_FAILED$"):
            c.subscriptions()
        self.assertEqual(opener.calls, [])
        self.assertEqual(b.requests, 1)

    def test_default_opener_disables_environment_proxies_and_binds_verified_tls(self):
        context = object()
        opener = Opener(Response())
        b = transport.Budget(dict(CAPS))
        client = transport.Reader(lambda: "SYNTHETIC_TOKEN", b, deadline=lambda _: nullcontext())
        with (patch.object(transport.ssl, "create_default_context", return_value=context) as tls,
              patch.object(transport.urllib.request, "build_opener", return_value=opener) as build):
            self.assertEqual(client.subscriptions(), {})
        tls.assert_called_once_with()
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], transport.NoRedirect)
        self.assertIs(handlers[2]._context, context)

    def test_real_alarm_interrupts_blocked_operation_and_restores_handler(self):
        import signal
        old = signal.getsignal(signal.SIGALRM)
        with self.assertRaisesRegex(BatchError, "TIME_BUDGET"):
            with transport.hard_deadline(.02):
                time.sleep(.2)
        self.assertEqual(signal.getsignal(signal.SIGALRM), old)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))
