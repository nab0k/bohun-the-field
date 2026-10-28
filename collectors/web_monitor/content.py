"""Bounded, allowlisted HTML capture. No discovery expansion, retries or trust flags."""
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.robotparser import RobotFileParser

from collectors.intelligence_pipeline.contracts import aware, canonical_url, nonempty

MAX_HTML = 2 * 1024 * 1024
MAX_TEXT = 128 * 1024
USER_AGENT = "BOHUN-Evidence/1.0"


@dataclass(frozen=True)
class WebPolicy:
    allowed_urls: tuple
    rights_reference: str
    primary: bool = False

    def __post_init__(self):
        nonempty(self.rights_reference)
        if (type(self.primary) is not bool or not self.primary or type(self.allowed_urls) is not tuple
                or not 1 <= len(self.allowed_urls) <= 5 or len(set(self.allowed_urls)) != len(self.allowed_urls)):
            raise ValueError("PRIMARY_WEB_ALLOWLIST_REQUIRED")
        for url in self.allowed_urls:
            if len(url)>8192 or canonical_url(url) != url or urlsplit(url).port not in (None, 443):
                raise ValueError("CANONICAL_HTTPS_ALLOWLIST_REQUIRED")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("WEB_REDIRECT_REQUIRES_REVIEW")


def _public_host(url):
    host = urlsplit(url).hostname
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError("WEB_NONPUBLIC_HOST")


def download(url, limit, *, html=False):
    _public_host(url)
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
    try:
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            if response.geturl() != url or response.status != 200:
                raise ValueError("WEB_RESPONSE_NOT_EXACT")
            if html and response.headers.get_content_type() not in {"text/html", "application/xhtml+xml"}:
                raise ValueError("WEB_CONTENT_TYPE_INVALID")
            body = response.read(limit + 1)
            if len(body) > limit:
                raise ValueError("WEB_BYTES_EXCEEDED")
            return body.decode("utf-8")
    except Exception:
        raise ValueError("WEB_FETCH_FAILED_NO_RETRY") from None


def extract_html(html):
    if importlib.metadata.version("trafilatura") != "2.3.1":
        raise ValueError("REVIEWED_TRAFILATURA_VERSION_REQUIRED")
    from trafilatura import extract
    return extract(html, output_format="txt", include_comments=False, include_tables=True, favor_precision=True)


def capture(url, policy, *, fetch=download, extractor=extract_html, clock=None):
    if url not in policy.allowed_urls:
        raise ValueError("WEB_URL_NOT_ALLOWLISTED")
    p = urlsplit(url)
    robots_url = f"https://{p.netloc}/robots.txt"
    robots = RobotFileParser(); robots.parse(fetch(robots_url, 128 * 1024).splitlines())
    if not robots.can_fetch(USER_AGENT, url):
        raise ValueError("WEB_ROBOTS_DENIED")
    # No parallel crawl: this captures one exact reviewed article URL only.
    html = fetch(url, MAX_HTML, html=True)
    if len(html.encode()) > MAX_HTML:
        raise ValueError("WEB_BYTES_EXCEEDED")
    text = extractor(html)
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > MAX_TEXT:
        raise ValueError("WEB_TEXT_EMPTY_OR_TOO_LARGE")
    return {"url": url, "received_at": aware((clock or (lambda: datetime.now(timezone.utc)))()).isoformat(),
            "html_sha256": hashlib.sha256(html.encode()).hexdigest(), "text": text,
            "text_sha256": hashlib.sha256(text.encode()).hexdigest(), "extractor": "trafilatura-2.3.1"}
