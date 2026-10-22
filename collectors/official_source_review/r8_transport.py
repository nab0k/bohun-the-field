"""Opt-in, bounded HTTPS transport for UK OCDS API. Never runs on import.

No secrets, data storage or scheduling. Real calls must be manually authorized.
The caller controls rate limiting; this wrapper limits retries per request.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
import time


class TransportError(RuntimeError):
    pass


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Do not follow a server redirect outside the approved origin.


ALLOWED_HOST = "www.find-tender.service.gov.uk"
ALLOWED_PATH = "/api/1.0/ocdsReleasePackages"


def retry_after_seconds(value: str | None, *, now: datetime | None = None, ceiling: float = 120.0) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        secs = float(int(value.strip()))
    except ValueError:
        try:
            then = parsedate_to_datetime(value)
            if then.tzinfo is None:
                return None
            secs = (then - (now or datetime.now(timezone.utc))).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    if secs < 0:
        raise TransportError("invalid_negative_retry_after")
    if secs > ceiling:
        # Honor the server: refusing is safer than retrying before Retry-After.
        raise TransportError("retry_after_exceeds_budget")
    return max(0., secs)


def bounded_get(url: str, *, opener=None, sleeper=time.sleep, timeout: float = 15.,
                max_attempts: int = 4, max_bytes: int = 8_000_000) -> bytes:
    p = urlsplit(url)
    if (p.scheme != "https" or p.hostname != ALLOWED_HOST or p.path != ALLOWED_PATH
            or p.port is not None or p.username is not None or p.password is not None or p.fragment):
        raise TransportError("url_not_official_api")
    if not 0 < timeout <= 60 or not 1 <= max_attempts <= 5 or max_bytes <= 0:
        raise TransportError("transport_limits_invalid")
    if opener is None:
        opener = build_opener(_RejectRedirects).open
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "BOHUN-Research/0.1"})
    for attempt in range(max_attempts):
        try:
            with opener(req, timeout=timeout) as response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise TransportError("response_over_limit")
                return data
        except HTTPError as exc:
            if exc.code not in (429, 503) or attempt + 1 == max_attempts:
                raise TransportError(f"source_http_{exc.code}") from None
            headers = getattr(exc, "headers", {})
            after = retry_after_seconds(headers.get("Retry-After"))
            if after is None:
                after = min(60.0, 2.0 ** attempt)
            sleeper(after)
        except (TimeoutError, URLError, OSError) as exc:
            if attempt + 1 == max_attempts:
                raise TransportError("source_transport_exhausted") from exc
            sleeper(min(60.0, 2.0 ** attempt))
    raise TransportError("source_transport_exhausted")
