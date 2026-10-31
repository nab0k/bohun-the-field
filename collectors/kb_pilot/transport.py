"""Isolated HTTPS fetch (A-11 S2, written by Codex 30.10.2026 in local-runtime/experiments/A11-input-20261029/
s2-prototype/transport.py, 24 offline tests there); moved here unchanged for the daily chain (D-20261031-A11-S2-FETCH).

No CLI, retries, redirects, proxy or database access. The parent bounds the whole child (including DNS/headers)
because socket timeouts alone do not bound trickling protocol reads.
"""
import base64
import http.client
import ipaddress
import json
import multiprocessing
from pathlib import Path
import re
import socket
import ssl
import tempfile
import time
from urllib.parse import urlsplit


class FetchError(ValueError):
    pass


def target(url):
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in url):
        raise FetchError("URL_INVALID")
    try:
        p = urlsplit(url)
        host = p.hostname
        if (p.scheme != "https" or not host or p.netloc != host or p.username or p.password
                or p.fragment or p.port is not None or not p.path.startswith("/")
                or len(host) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", x)
                                         for x in host.split("."))):
            raise FetchError("URL_INVALID")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise FetchError("IP_LITERAL_DENIED")
        return host, p.path + ("?" + p.query if p.query else "")
    except ValueError:
        raise FetchError("URL_INVALID") from None


def public(address):
    ip = ipaddress.ip_address(address)
    return (ip.is_global and not ip.is_multicast and not ip.is_reserved
            and not ip.is_unspecified and not ip.is_loopback and not ip.is_link_local
            and not getattr(ip, "ipv4_mapped", None))


def context():
    ctx = ssl.create_default_context()
    if not ctx.check_hostname or ctx.verify_mode != ssl.CERT_REQUIRED:
        raise FetchError("TLS_CONFIGURATION")
    return ctx


def remaining(end, clock):
    value = end - clock()
    if value <= 0:
        raise FetchError("DEADLINE")
    return value


def once(url, limit, timeout, *, html=True, resolver=None, socket_factory=None,
         tls_context=None, response_factory=None, clock=None):
    """One request. Internal: must run under the supervising parent for live use."""
    resolver = resolver or socket.getaddrinfo
    socket_factory = socket_factory or socket.socket
    response_factory = response_factory or http.client.HTTPResponse
    clock = clock or time.monotonic
    host, path = target(url)
    end = clock() + timeout
    raw = secure = response = None
    try:
        addresses = resolver(host, 443, type=socket.SOCK_STREAM)
        remaining(end, clock)
        if not addresses or any(row[0] not in (socket.AF_INET, socket.AF_INET6)
                                or not public(row[4][0]) for row in addresses):
            raise FetchError("NONPUBLIC_DNS")
        family, _, _, _, sockaddr = sorted(addresses, key=lambda row: (row[0], row[4][0]))[0]
        ip = sockaddr[0]
        # Reconstruct numeric sockaddr, never pass a hostname to connect().
        address = (ip, 443) if family == socket.AF_INET else (ip, 443, 0, 0)
        raw = socket_factory(family, socket.SOCK_STREAM)
        raw.settimeout(remaining(end, clock))
        raw.connect(address)
        if ipaddress.ip_address(raw.getpeername()[0]) != ipaddress.ip_address(ip):
            raise FetchError("PEER_MISMATCH")
        ctx = tls_context or context()
        if not ctx.check_hostname or ctx.verify_mode != ssl.CERT_REQUIRED:
            raise FetchError("TLS_CONFIGURATION")
        raw.settimeout(remaining(end, clock))
        secure = ctx.wrap_socket(raw, server_hostname=host)
        if ipaddress.ip_address(secure.getpeername()[0]) != ipaddress.ip_address(ip):
            raise FetchError("PEER_MISMATCH")
        secure.settimeout(remaining(end, clock))
        secure.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\n"
                        "User-Agent: BOHUN-Evidence/1.0\r\nAccept: text/html,text/plain\r\n"
                        "Accept-Encoding: identity\r\nConnection: close\r\n\r\n").encode("ascii"))
        response = response_factory(secure)
        secure.settimeout(remaining(end, clock))
        response.begin()
        remaining(end, clock)
        if response.status != 200:
            raise FetchError("HTTP_" + str(response.status))
        headers = response.headers
        enc = headers.get_all("Content-Encoding", [])
        if len(enc) > 1 or (enc and enc[0].strip().lower() != "identity"):
            raise FetchError("CONTENT_ENCODING")
        lengths = headers.get_all("Content-Length", [])
        transfer = headers.get_all("Transfer-Encoding", [])
        if len(lengths) > 1 or len(transfer) > 1 or (transfer and lengths):
            raise FetchError("FRAMING_INVALID")
        if transfer and (transfer[0].strip().lower() != "chunked" or not response.chunked):
            raise FetchError("TRANSFER_ENCODING_UNSUPPORTED")
        expected = None
        if lengths:
            if not re.fullmatch(r"[0-9]+", lengths[0]) or len(lengths[0]) > 12:
                raise FetchError("FRAMING_INVALID")
            expected = int(lengths[0])
            if expected > limit:
                raise FetchError("BYTES_EXCEEDED")
        types = headers.get_all("Content-Type", [])
        if len(types) != 1 or (html and headers.get_content_type() not in ("text/html", "application/xhtml+xml")):
            raise FetchError("CONTENT_TYPE")
        charset = headers.get_content_charset()
        if charset and charset.lower() not in ("utf-8", "utf8"):
            raise FetchError("CHARSET_INVALID")
        data = bytearray()
        while True:
            secure.settimeout(remaining(end, clock))
            part = response.read1(min(65536, limit + 1 - len(data)))
            remaining(end, clock)
            if not part:
                break
            data.extend(part)
            if len(data) > limit:
                raise FetchError("BYTES_EXCEEDED")
        if expected is not None and len(data) != expected:
            raise FetchError("BODY_TRUNCATED")
        data.decode("utf-8")
        return {"status": 200, "url": url, "body": bytes(data),
                "content_type": headers.get_content_type()}
    except FetchError:
        raise
    except Exception:
        raise FetchError("FETCH_FAILED_NO_RETRY") from None
    finally:
        for resource in (response, secure, raw):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    pass


def worker(filename, url, limit, timeout, html):
    try:
        result = once(url, limit, timeout, html=html)
        result["body"] = base64.b64encode(result["body"]).decode("ascii")
        payload = {"ok": result}
    except FetchError as exc:
        payload = {"error": str(exc)}
    except Exception:
        payload = {"error": "WORKER_FAILED"}
    Path(filename).write_text(json.dumps(payload), encoding="utf-8")


def fetch(url, allowed_urls, *, limit=2 * 1024 * 1024, timeout=20, html=True,
          process_context=None, clock=None):
    """Public entry. One isolated process, no retry; private temporary IPC file.

    Admission to an external request ledger is the runner's responsibility.
    Never call without the separately approved live manifest.
    """
    target(url)
    if url not in allowed_urls:
        raise FetchError("URL_NOT_ALLOWLISTED")
    if type(limit) is not int or not 1 <= limit <= 2 * 1024 * 1024:
        raise FetchError("LIMIT_INVALID")
    if type(timeout) not in (int, float) or not 0 < timeout <= 20:
        raise FetchError("TIMEOUT_INVALID")
    clock = clock or time.monotonic
    end = clock() + timeout
    cleanup_reserve = min(0.5, timeout / 4)
    work_end = end - cleanup_reserve
    ctx = process_context or multiprocessing.get_context("spawn")
    with tempfile.TemporaryDirectory(prefix="bohun-s2-") as folder:
        filename = str(Path(folder) / "receipt.json")
        process = ctx.Process(target=worker, args=(filename, url, limit, timeout - cleanup_reserve, html))
        started = False
        try:
            process.start()
            started = True
            process.join(remaining(work_end, clock))
            if process.is_alive():
                raise FetchError("DEADLINE")
            remaining(work_end, clock)
            if process.exitcode != 0 or not Path(filename).is_file():
                raise FetchError("WORKER_FAILED")
            if Path(filename).stat().st_size > 3 * 1024 * 1024:
                raise FetchError("IPC_BYTES_EXCEEDED")
            result = json.loads(Path(filename).read_text(encoding="utf-8"))
            if "error" in result:
                raise FetchError(result["error"])
            result = result["ok"]
            result["body"] = base64.b64decode(result["body"], validate=True)
            if len(result["body"]) > limit or result["url"] != url or result["status"] != 200:
                raise FetchError("IPC_INVALID")
            return result
        finally:
            if started:
                if process.is_alive():
                    process.terminate()
                    process.join(cleanup_reserve / 2)
                if process.is_alive():
                    process.kill()
                    process.join(cleanup_reserve / 2)
                if process.is_alive():
                    raise FetchError("CHILD_CLEANUP_FAILED")
                process.close()
