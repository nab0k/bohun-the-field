"""Bounded read-only Inoreader transport; no OAuth, retry, DB or import-time I/O."""

from contextlib import contextmanager
import hashlib
import math
import signal
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .batch_delivery import BatchError, require, parse_json, CAPS


class Budget:
    def __init__(self, caps, *, clock=time.monotonic):
        require(isinstance(caps, dict) and set(caps) == set(CAPS), "CAPS_SHAPE")
        for name, maximum in CAPS.items():
            require(type(caps[name]) is int and (caps[name] == maximum if name in
                    {"concurrency", "retries", "authenticated_redirects"} else 1 <= caps[name] <= maximum), "CAPS_LIMIT")
        self.caps, self.clock = dict(caps), clock
        self.started = clock()
        self.requests = self.bytes = 0

    def remaining(self):
        elapsed = self.clock() - self.started
        require(math.isfinite(elapsed) and elapsed >= 0, "TIME_BUDGET")
        seconds = self.caps["seconds"] - elapsed
        require(math.isfinite(seconds) and seconds > 0, "TIME_BUDGET")
        return seconds

    def reserve(self):
        self.remaining()
        require(self.requests < self.caps["requests"], "REQUEST_BUDGET")
        require(self.bytes < self.caps["total_bytes"], "BYTE_BUDGET")
        self.requests += 1

    def charge(self, count):
        self.bytes += count
        require(self.bytes <= self.caps["total_bytes"], "BYTE_BUDGET")
        self.remaining()

    def receipt(self):
        return dict(requests=self.requests, response_body_bytes=self.bytes,
                    elapsed_seconds=max(0, self.clock()-self.started), caps=self.caps)


@contextmanager
def hard_deadline(seconds):
    require(threading.current_thread() is threading.main_thread()
            and hasattr(signal, "setitimer"), "HARD_DEADLINE_UNAVAILABLE")
    require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), "HARD_DEADLINE_BUSY")
    old = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise BatchError("TIME_BUDGET")
    signal.signal(signal.SIGALRM, expired)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fp.close()
        raise BatchError("AUTH_REDIRECT_FORBIDDEN")


class Reader:
    API = "https://www.inoreader.com/reader/api/0/"

    def __init__(self, token_provider, budget, *, opener=None, deadline=hard_deadline):
        require(callable(token_provider), "TOKEN_PROVIDER_REQUIRED")
        self.token_provider, self.budget, self.deadline = token_provider, budget, deadline
        self.opener = opener
        self.receipts = []

    def _read(self, path):
        self.budget.reserve()
        response = None
        start_bytes = self.budget.bytes
        hasher = hashlib.sha256()
        receipt = dict(request_number=self.budget.requests, method="GET",
                       request_path_sha256=hashlib.sha256(path.encode()).hexdigest(),
                       http_status=None, result="PENDING", body_complete=False)
        self.receipts.append(receipt)
        try:
            with self.deadline(min(10, self.budget.remaining())):
                token = self.token_provider()
                require(isinstance(token, str) and 0 < len(token) <= 8192
                        and not any(ord(c) < 33 or ord(c) > 126 for c in token), "TOKEN_INVALID")
                if self.opener is None:
                    self.opener = urllib.request.build_opener(
                        urllib.request.ProxyHandler({}), NoRedirect(),
                        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
                request = urllib.request.Request(self.API + path, headers={
                    "Authorization": "Bearer " + token, "User-Agent": "BOHUN-OriginPreview/1"})
                response = self.opener.open(request, timeout=min(10, self.budget.remaining()))
                self.budget.remaining()
                receipt["http_status"] = response.getcode()
                require(receipt["http_status"] == 200, "HTTP_STATUS")
                raw_length = response.headers.get("Content-Length")
                length = None
                if raw_length is not None:
                    require(isinstance(raw_length, str) and raw_length.isdecimal(), "HTTP_LENGTH")
                    length = int(raw_length)
                cap = min(self.budget.caps["response_bytes"], self.budget.caps["total_bytes"]-self.budget.bytes)
                require(length is None or 0 <= length <= cap, "RESPONSE_LIMIT")
                chunks, size = [], 0
                while length is None or size < length:
                    remaining = cap-size if length is None else length-size
                    require(remaining > 0, "RESPONSE_LIMIT")
                    chunk = response.read(min(65536, remaining))
                    require(isinstance(chunk, bytes), "HTTP_BODY")
                    if not chunk:
                        break
                    size += len(chunk)
                    hasher.update(chunk)
                    self.budget.charge(len(chunk))
                    require(size <= cap, "RESPONSE_LIMIT")
                    chunks.append(chunk)
                require(length is None or size == length, "HTTP_TRUNCATED")
                self.budget.remaining()
                receipt["body_complete"] = True
                value = parse_json(b"".join(chunks), maximum=self.budget.caps["response_bytes"])
                receipt["result"] = "OK"
                return value
        except BatchError as exc:
            receipt["result"] = str(exc)
            raise
        except urllib.error.HTTPError as exc:
            receipt.update(http_status=exc.code, result="HTTP_STATUS")
            exc.close()
            raise BatchError("HTTP_STATUS") from None
        except Exception:
            receipt["result"] = "HTTP_READ_FAILED"
            raise BatchError("HTTP_READ_FAILED") from None
        finally:
            receipt.update(response_body_bytes=self.budget.bytes-start_bytes,
                           body_sha256=hasher.hexdigest())
            if response is not None:
                try:
                    response.close()
                except Exception:
                    receipt["result"] = "HTTP_CLOSE_FAILED"
                    raise BatchError("HTTP_CLOSE_FAILED") from None

    def subscriptions(self):
        return self._read("subscription/list?output=json")

    def stream(self, feed_id):
        require(isinstance(feed_id, str) and feed_id.startswith("feed/")
                and len(feed_id) <= 4096, "FEED_ID_INVALID")
        return self._read("stream/contents/" + urllib.parse.quote(feed_id, safe="") + "?n=1&output=json")
