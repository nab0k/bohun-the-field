"""Codex's bounded HTTP with one addition found on the 18.11 shadow run: gzip/deflate bodies.

Some servers send a compressed body even to `Accept-Encoding: identity` (bundeswehr.de); Codex's client then stops
with ENCODING_UNSUPPORTED and the site is wrongly rejected as unreachable. Here the compressed bytes are capped as
before and the decompressed size has its own cap. Everything else (pinned public address, robots, 2 s per host,
budgets, same-origin redirects only) is Codex's code unchanged.
"""
import http.client
import ipaddress
import socket
import sys
import threading
import zlib
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_discovery import radars  # noqa: E402
from codex_discovery.policy import Problem, digest, require  # noqa: E402

MAX_DECODED = 4_000_000


def decode(body, encoding):
    encoding = (encoding or "identity").lower().strip()
    if encoding in ("", "identity"):
        return body
    require(encoding in ("gzip", "x-gzip", "deflate"), "ENCODING_UNSUPPORTED")
    wbits = 16 + zlib.MAX_WBITS if "gzip" in encoding else zlib.MAX_WBITS
    try:
        d = zlib.decompressobj(wbits)
        out = d.decompress(body, MAX_DECODED + 1)
    except zlib.error:
        if encoding == "deflate":  # raw deflate without zlib header
            try:
                d = zlib.decompressobj(-zlib.MAX_WBITS)
                out = d.decompress(body, MAX_DECODED + 1)
            except zlib.error:
                raise Problem("ENCODING_INVALID") from None
        else:
            raise Problem("ENCODING_INVALID") from None
    require(len(out) <= MAX_DECODED and not d.unconsumed_tail, "RESPONSE_SIZE")
    return out


class HTTP(radars.HTTP):
    def _one(self, url, method, headers, body, limit, api):
        require(self.enabled, "NETWORK_DISABLED")
        require(not self.dns_failed, "DNS_WORKER_HOLD")
        self.budget.request()
        if api:
            require(self.reserve_api is not None, "API_LEDGER_REQUIRED")
            self.reserve_api(digest(url))
        host = urlsplit(url).hostname
        wait = 2 - (self.budget.clock() - self.last_host.get(host, -1e9))
        if wait > 0:
            require(wait < self.budget.remaining(), "CYCLE_TIME_CAP")
            self.sleep(wait)
        started = self.budget.clock()
        try:
            address, family = self.resolver(host, min(12, self.budget.remaining()))
        except Problem as exc:
            if str(exc) == "DNS_TIMEOUT":
                self.dns_failed = True
            raise
        require(ipaddress.ip_address(address).is_global, "DNS_PRIVATE")
        timeout = min(12 - (self.budget.clock() - started), self.budget.remaining())
        require(timeout > 0, "REQUEST_TIMEOUT")
        conn = self.connection_factory(host, address, family, timeout)

        def abort():
            try:
                if conn.sock is not None and hasattr(conn.sock, "shutdown"):
                    conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            finally:
                conn.close()
        cutoff = threading.Timer(timeout, abort)
        cutoff.daemon = True
        cutoff.start()
        data = bytearray()
        try:
            target = urlsplit(url).path or "/"
            if urlsplit(url).query:
                target += "?" + urlsplit(url).query
            conn.request(method, target, body=body,
                         headers={"User-Agent": radars.UA, "Accept-Encoding": "identity", **headers})
            response = conn.getresponse()
            metadata = {k.lower(): v for k, v in response.getheaders()}
            length = metadata.get("content-length")
            require(length is None or (length.isdigit() and int(length) <= limit), "RESPONSE_SIZE")
            while True:
                left = min(12 - (self.budget.clock() - started), self.budget.remaining())
                require(left > 0, "REQUEST_TIMEOUT")
                if conn.sock is not None:
                    conn.sock.settimeout(left)
                block = response.read1(min(16384, limit + 1 - len(data)))
                if not block:
                    break
                data.extend(block)
                self.budget.add_bytes(len(block))
                require(len(data) <= limit, "RESPONSE_SIZE")
            payload = decode(bytes(data), metadata.get("content-encoding"))
            metadata.pop("content-encoding", None)
            self.last_host[host] = self.budget.clock()
            self.budget.trace.append({"url_hash": digest(url), "method": method,
                                      "status": response.status, "bytes": len(data)})
            return radars.Response(response.status, payload, url, metadata)
        except (OSError, http.client.HTTPException, UnicodeError, ValueError) as exc:
            if isinstance(exc, Problem):
                raise
            raise Problem("TRANSPORT_FAILURE") from None
        finally:
            cutoff.cancel()
            self.last_host[host] = self.budget.clock()
            conn.close()
