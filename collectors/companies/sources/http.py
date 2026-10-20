"""Minimal JSON HTTP client with timeouts, bounded retries and a response-size cap (stdlib only).

Adapters receive an object with get_json(url) / post_json(url, body); tests pass a fake.
"""
import json
import time
import urllib.error
import urllib.request
import uuid

USER_AGENT = "BohundefenceCompanies/0.1 (+https://bohundefence.com; research collector)"
MAX_BYTES = 5 * 1024 * 1024
RETRY_STATUS = {429, 500, 502, 503, 504}


class SourceError(RuntimeError):
    pass


class JsonHttp:
    def __init__(self, timeout=45, retries=2, backoff=2.0, pause=0.0, opener=urllib.request.urlopen):
        self.timeout, self.retries, self.backoff, self.pause, self._open = timeout, retries, backoff, pause, opener

    def _request(self, req, raw=False):
        want_raw = raw
        last = None
        for attempt in range(self.retries + 1):
            try:
                with self._open(req, timeout=self.timeout) as resp:
                    raw = resp.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise SourceError(f"response over {MAX_BYTES} bytes: {req.full_url}")
                if self.pause:
                    time.sleep(self.pause)
                return raw if want_raw else json.loads(raw.decode("utf-8"))
            except urllib.error.HTTPError as err:
                last = err
                if err.code == 404:
                    return None
                if err.code not in RETRY_STATUS or attempt == self.retries:
                    raise SourceError(f"HTTP {err.code} {req.full_url}") from err
            except (urllib.error.URLError, TimeoutError) as err:
                last = err
                if attempt == self.retries:
                    raise SourceError(f"network error {req.full_url}: {err}") from err
            time.sleep(self.backoff * (attempt + 1))
        raise SourceError(str(last))

    def get_json(self, url, accept="application/json"):
        return self._request(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept}))

    def get_bytes(self, url, accept="*/*"):
        """Raw body (files such as xlsx), same size cap and retries; 404 -> None."""
        return self._request(urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept}),
                             raw=True)

    def post_form(self, url, fields):
        """multipart/form-data POST where every field is a JSON value (EU Funding & Tenders Search API)."""
        boundary = "----bohun" + uuid.uuid4().hex
        chunks = []
        for name, value in fields.items():
            chunks += [f"--{boundary}\r\n".encode(),
                       f'Content-Disposition: form-data; name="{name}"; filename="blob"\r\n'.encode(),
                       b"Content-Type: application/json\r\n\r\n",
                       json.dumps(value, separators=(",", ":")).encode(), b"\r\n"]
        chunks.append(f"--{boundary}--\r\n".encode())
        return self._request(urllib.request.Request(url, data=b"".join(chunks), method="POST", headers={
            "User-Agent": USER_AGENT, "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}"}))

    def post_json(self, url, body):
        data = json.dumps(body).encode("utf-8")
        return self._request(urllib.request.Request(url, data=data, method="POST", headers={
            "User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"}))
