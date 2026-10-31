from email.message import Message
import json
import io
from pathlib import Path
import socket
import ssl
import unittest
from unittest.mock import Mock, patch

from collectors.kb_pilot import transport as t

URL = "https://publisher.example/article?a=1"
ADDRESS = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))


class Sock:
    def __init__(self, peer="93.184.216.34"):
        self.peer = peer
        self.closed = False
        self.sent = []
        self.connected = []
        self.timeouts = []

    def settimeout(self, value): self.timeouts.append(value)
    def connect(self, address): self.connected.append(address)
    def getpeername(self): return self.peer, 443
    def sendall(self, data): self.sent.append(data)
    def close(self): self.closed = True


class Context:
    check_hostname = True
    verify_mode = ssl.CERT_REQUIRED

    def __init__(self, secure): self.secure = secure; self.names = []
    def wrap_socket(self, raw, server_hostname):
        self.names.append(server_hostname)
        return self.secure


class Response:
    chunked = False
    def __init__(self, data=b"article", headers=None, status=200):
        self.status = status; self.data = data; self.closed = False
        self.headers = Message()
        for k, v in (headers if headers is not None else [("Content-Type", "text/html")]):
            self.headers[k] = v

    def begin(self): pass
    def read1(self, size):
        part = self.data[:size]; self.data = self.data[size:]; return part
    def close(self): self.closed = True


class Tests(unittest.TestCase):
    def setUp(self):
        self.socket_block = patch.object(socket, "socket", side_effect=AssertionError("NO NETWORK"))
        self.dns_block = patch.object(socket, "getaddrinfo", side_effect=AssertionError("NO DNS"))
        self.socket_block.start(); self.dns_block.start()

    def tearDown(self):
        self.socket_block.stop(); self.dns_block.stop()

    def call(self, response=None, *, addresses=None, raw=None, secure=None, ctx=None, clock=lambda: 0, limit=100):
        raw = raw or Sock(); secure = secure or Sock(); ctx = ctx or Context(secure)
        response = response or Response()
        resolver = Mock(return_value=addresses if addresses is not None else [ADDRESS])
        factory = Mock(return_value=raw)
        try:
            result = t.once(URL, limit, 20, resolver=resolver, socket_factory=factory,
                            tls_context=ctx, response_factory=lambda _: response, clock=clock)
            return result, resolver, raw, secure, ctx, factory
        finally:
            self.assertTrue(raw.closed or not factory.called)
            if secure.sent: self.assertTrue(response.closed)

    def test_numeric_connect_sni_host_and_no_proxy(self):
        with patch.dict("os.environ", {"HTTPS_PROXY": "http://SECRET:pass@private/"}):
            result, dns, raw, secure, ctx, factory = self.call()
        self.assertEqual(dns.call_count, 1); self.assertEqual(factory.call_count, 1)
        self.assertEqual(raw.connected, [("93.184.216.34", 443)])
        self.assertEqual(ctx.names, ["publisher.example"])
        self.assertEqual(result["body"], b"article")
        wire = b"".join(secure.sent)
        self.assertIn(b"GET /article?a=1 HTTP/1.1", wire)
        self.assertIn(b"Host: publisher.example\r\n", wire)
        for key in (b"SECRET", b"Cookie:", b"Authorization:", b"Proxy-"):
            self.assertNotIn(key, wire)
        self.assertTrue(secure.closed)

    def test_actual_context_requires_ca_and_hostname(self):
        ctx = t.context()
        self.assertTrue(ctx.check_hostname)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)

    def test_dns_admission(self):
        for ip in ("127.0.0.1", "10.0.0.1", "169.254.1.1", "224.0.0.1", "::1", "::ffff:8.8.8.8", "0.0.0.0"):
            self.assertFalse(t.public(ip))
            with self.subTest(ip=ip), self.assertRaisesRegex(t.FetchError, "NONPUBLIC_DNS"):
                self.call(addresses=[ADDRESS, (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))])
        with self.assertRaisesRegex(t.FetchError, "NONPUBLIC_DNS"):
            self.call(addresses=[])

    def test_peers_rejected_before_get(self):
        for where in ("raw", "secure"):
            raw = Sock("127.0.0.1") if where == "raw" else Sock()
            secure = Sock("127.0.0.1") if where == "secure" else Sock()
            with self.assertRaisesRegex(t.FetchError, "PEER_MISMATCH"):
                self.call(raw=raw, secure=secure)
            self.assertEqual(secure.sent, [])
            self.assertTrue(raw.closed)

    def test_bad_certificate_and_unsafe_context(self):
        secure = Sock(); ctx = Context(secure)
        ctx.wrap_socket = Mock(side_effect=ssl.SSLCertVerificationError("SECRET_URL"))
        with self.assertRaisesRegex(t.FetchError, "FETCH_FAILED_NO_RETRY") as error:
            self.call(secure=secure, ctx=ctx)
        self.assertNotIn("SECRET", str(error.exception)); self.assertEqual(secure.sent, [])
        ctx = Context(secure); ctx.check_hostname = False
        with self.assertRaisesRegex(t.FetchError, "TLS_CONFIGURATION"):
            self.call(secure=secure, ctx=ctx)

    def test_status_errors_do_not_retry(self):
        for status in (301, 302, 401, 403, 429, 500):
            response = Response(status=status)
            with self.subTest(status=status), self.assertRaisesRegex(t.FetchError, "HTTP_" + str(status)):
                self.call(response)
            self.assertTrue(response.closed)

    def test_framing_and_encoding(self):
        cases = [([("Content-Length", "1"), ("Content-Length", "1")], "FRAMING"),
                 ([("Content-Length", "1"), ("Transfer-Encoding", "chunked")], "FRAMING"),
                 ([("Transfer-Encoding", "chunked")], "TRANSFER_ENCODING"),
                 ([("Content-Length", "-1")], "FRAMING"),
                 ([("Content-Length", "999")], "BYTES"),
                 ([("Content-Encoding", "gzip")], "CONTENT_ENCODING"),
                 ([("Content-Type", "text/html; charset=latin1")], "CHARSET"),
                 ([("Content-Type", "application/json")], "CONTENT_TYPE")]
        for headers, reason in cases:
            h = headers if any(k == "Content-Type" for k, _ in headers) else [("Content-Type", "text/html")] + headers
            with self.subTest(reason=reason), self.assertRaisesRegex(t.FetchError, reason):
                self.call(Response(headers=h))

    def test_stream_limit_and_content_length_truncation(self):
        self.assertEqual(self.call(Response(b"x" * 100))[0]["body"], b"x" * 100)
        for response, reason in ((Response(b"x" * 101), "BYTES"),
                                 (Response(b"tiny", [("Content-Type", "text/html"), ("Content-Length", "20")]), "TRUNCATED"),
                                 (Response(b"\xff"), "FETCH_FAILED")):
            with self.assertRaisesRegex(t.FetchError, reason): self.call(response)

    def test_actual_http_parser_with_chunked_wire(self):
        prefix = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nTransfer-Encoding: chunked\r\n\r\n"
        for wire, reason in ((b"3\r\nabc\r\n0\r\n\r\n", None),
                             (b"z\r\nabc\r\n", "FETCH_FAILED"),
                             (b"6\r\nabc", "FETCH_FAILED"),
                             (b"4\r\nabcd\r\n0\r\n\r\n", "BYTES")):
            raw = Sock(); secure = Sock()
            secure.makefile = lambda *_: io.BytesIO(prefix + wire)
            options = dict(resolver=lambda *_args, **_kw: [ADDRESS],
                           socket_factory=lambda *_: raw, tls_context=Context(secure), clock=lambda: 0)
            if reason:
                with self.subTest(reason=reason), self.assertRaisesRegex(t.FetchError, reason):
                    t.once(URL, 3, 20, **options)
            else:
                self.assertEqual(t.once(URL, 3, 20, **options)["body"], b"abc")
            self.assertTrue(raw.closed); self.assertTrue(secure.closed)

    def test_shared_deadline_not_reset_by_trickle(self):
        now = [0]
        class Trickle(Response):
            def read1(self, size): now[0] += 7; return b"x"
        raw = Sock(); secure = Sock()
        with self.assertRaisesRegex(t.FetchError, "DEADLINE"):
            self.call(Trickle(), raw=raw, secure=secure, clock=lambda: now[0])
        self.assertEqual(secure.timeouts[-1], 6)
        self.assertTrue(raw.closed); self.assertTrue(secure.closed)

    def test_dns_timeout_no_connect(self):
        clock = iter((0, 21))
        with self.assertRaisesRegex(t.FetchError, "DEADLINE"):
            self.call(clock=lambda: next(clock))

    def test_target_and_input_checks(self):
        for url in ("http://publisher.example/a", "https://127.0.0.1/a", "https://publisher.example:443/a",
                    "https://user@publisher.example/a", URL + "#x", URL + "\r\n", "https://publisher.example/é"):
            with self.subTest(url=url), self.assertRaises(t.FetchError): t.target(url)
        with self.assertRaisesRegex(t.FetchError, "ALLOWLIST"):
            t.fetch(URL, ())
        for limit in (0, True, 2 * 1024 * 1024 + 1):
            with self.assertRaises(t.FetchError): t.fetch(URL, (URL,), limit=limit)

    def test_parent_worker_success_timeout_cleanup_and_error(self):
        for mode in ("success", "hung", "killed", "dns_error"):
            class Process:
                exitcode = 0
                def __init__(self, target, args):
                    self.args = args; self.alive = mode in ("hung", "killed"); self.closed = False
                    self.terminated = self.killed = False
                def start(self):
                    payload = {"ok": {"status": 200, "url": URL, "body": "eA==", "content_type": "text/html"}}
                    if mode == "dns_error": payload = {"error": "NONPUBLIC_DNS"}
                    Path(self.args[0]).write_text(json.dumps(payload))
                def join(self, timeout): pass
                def is_alive(self): return self.alive
                def terminate(self):
                    self.terminated = True
                    if mode != "killed": self.alive = False
                def kill(self): self.killed = True; self.alive = False
                def close(self): self.closed = True
            ctx = Mock(); instances = []
            def create(**kw):
                p = Process(**kw); instances.append(p); return p
            ctx.Process.side_effect = create
            if mode == "success":
                self.assertEqual(t.fetch(URL, (URL,), process_context=ctx)["body"], b"x")
            else:
                with self.assertRaisesRegex(t.FetchError, "NONPUBLIC_DNS" if mode == "dns_error" else "DEADLINE"):
                    t.fetch(URL, (URL,), process_context=ctx)
            self.assertEqual(len(instances), 1)
            self.assertTrue(instances[0].closed)
            self.assertFalse(Path(instances[0].args[0]).exists())
            if mode == "killed": self.assertTrue(instances[0].killed)


if __name__ == "__main__": unittest.main()
