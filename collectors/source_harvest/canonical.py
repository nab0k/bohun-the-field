"""Deterministic URL normalization and candidate identity helpers."""

import hashlib
import posixpath
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid"}
REJECT_HOSTS = {
    "x.com", "twitter.com", "facebook.com", "linkedin.com", "t.me",
    "telegram.me", "news.google.com", "feedly.com", "inoreader.com",
}


def canonical_url(value, *, force_https=True):
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if not host:
        return ""
    port = parsed.port
    netloc = host if not port or port in (80, 443) else f"{host}:{port}"
    scheme = "https" if force_https and parsed.scheme in ("http", "https") else parsed.scheme.lower()
    path = re.sub(r"/{2,}", "/", parsed.path or "")
    path = posixpath.normpath(path) if path else ""
    if path == "." or path == "/":
        path = ""
    query = urlencode(sorted(
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING
    ))
    return urlunsplit((scheme, netloc, path.rstrip("/"), query, ""))


def hostname(value):
    return (urlsplit(canonical_url(value)).hostname or "").lower()


def rejected_reason(value):
    host = hostname(value)
    if not host:
        return "invalid_url"
    if host in REJECT_HOSTS or any(host.endswith("." + item) for item in REJECT_HOSTS):
        return "social_or_aggregator"
    return ""


def stable_id(prefix, *parts):
    raw = "\x1f".join(str(part or "") for part in parts)
    return prefix + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def candidate_key(site_url, feed_url=""):
    site_host = hostname(site_url or feed_url)
    if feed_url:
        parsed = urlsplit(canonical_url(feed_url))
        return site_host, parsed.path.rstrip("/") or "/", parsed.query
    return site_host, "", ""

