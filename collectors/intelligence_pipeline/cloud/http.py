"""Bounded HTTPS JSON only. No redirects, retries, environment proxy or logging."""

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler


class CloudError(RuntimeError):
    """Only fixed non-sensitive codes may reach command-line output."""


@dataclass(frozen=True)
class Reply:
    status: int
    payload: dict


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class JsonHTTP:
    def __init__(self, host, *, timeout=45, max_response=512 * 1024):
        if host not in {"generativelanguage.googleapis.com", "api.notion.com"}:
            raise ValueError("HTTP_HOST_NOT_ALLOWED")
        self.host, self.timeout, self.max_response = host, timeout, max_response
        self.opener = build_opener(NoRedirect(), ProxyHandler({}))

    def request(self, method, path, headers, payload=None):
        if method not in {"GET", "POST"} or not path.startswith("/") or path.startswith("//"):
            raise CloudError("HTTP_REQUEST_REFUSED")
        url = "https://" + self.host + path
        if urlsplit(url).hostname != self.host or "#" in path:
            raise CloudError("HTTP_REQUEST_REFUSED")
        raw = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if raw is not None and len(raw) > 512 * 1024:
            raise CloudError("HTTP_REQUEST_TOO_LARGE")
        request = Request(url, data=raw, headers={**headers, "Content-Type": "application/json"}, method=method)
        try:
            try:
                response = self.opener.open(request, timeout=self.timeout)
            except HTTPError as exc:
                response = exc
            with response:
                data = response.read(self.max_response + 1)
                if len(data) > self.max_response:
                    raise CloudError("HTTP_RESPONSE_TOO_LARGE")
                body = json.loads(data)
                if not isinstance(body, dict):
                    raise CloudError("HTTP_RESPONSE_INVALID")
                return Reply(response.code, body)
        except (OSError, URLError, ValueError) as exc:
            raise CloudError("HTTP_RESULT_UNCERTAIN") from None
