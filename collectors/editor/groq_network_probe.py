"""C3-10 single unauthenticated Groq network probe.

One GET to the public *models metadata* endpoint. No key, no request body,
no inference, no Supabase. Never include response text or unrestricted header
values in the report. This probe can distinguish an API JSON rejection from
an upstream HTML rejection, but cannot prove which service blocked a request.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import urllib.error
import urllib.request

URL = "https://api.groq.com/openai/v1/models"
READ_CAP = 192
TIMEOUT_SECONDS = 20


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _classify_body(prefix: bytes) -> str:
    if not prefix:
        return "empty"
    data = prefix.lstrip().lower()
    if data.startswith((b"{", b"[")):
        return "json_like"  # Prefix classification, not JSON validation.
    if data.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "html_like"
    return "other"


def _safe_headers(headers) -> dict:
    """Only closed-class signals; never expose header values, IDs, IPs or URL."""
    def get(name):
        return headers.get(name) if headers is not None else None

    raw_type = get("Content-Type")
    mime = raw_type.split(";", 1)[0].strip().lower() if isinstance(raw_type, str) else ""
    if mime == "application/json" or mime.endswith("+json"):
        content_class = "json"
    elif mime == "text/html":
        content_class = "html"
    elif mime == "text/plain":
        content_class = "text"
    else:
        content_class = "other" if mime else "missing"
    server = get("Server")
    server_class = ("cloudflare" if isinstance(server, str) and server.lower().strip() == "cloudflare"
                    else "other" if server else "missing")
    challenge = get("cf-mitigated")
    return {
        "content_type_class": content_class,
        "server_class": server_class,
        "cf_ray_present": bool(get("cf-ray")),
        "cf_mitigated_challenge": isinstance(challenge, str) and challenge.strip().lower() == "challenge",
        "www_authenticate_present": bool(get("www-authenticate")),
        "redirect_location_present": bool(get("location")),
    }


def _response_info(status, headers, prefix: bytes) -> dict:
    return {
        "http_status": status if type(status) is int and 100 <= status <= 599 else None,
        **_safe_headers(headers),
        "body_prefix_class": _classify_body(prefix),
        "transport_error": "none",
    }


def _transport_error() -> dict:
    return {
        "http_status": None,
        **_safe_headers(None),
        "body_prefix_class": "unavailable",
        "transport_error": "network_or_tls_failure",
    }


def probe(*, opener=None) -> dict:
    """One bounded GET, no redirects. opener is injectable for offline tests."""
    if opener is None:
        opener = urllib.request.build_opener(_NoRedirect()).open
    req = urllib.request.Request(URL, method="GET", headers={"Accept": "application/json"})
    try:
        with opener(req, timeout=TIMEOUT_SECONDS) as reply:
            result = _response_info(reply.status, reply.headers, reply.read(READ_CAP))
    except urllib.error.HTTPError as error:
        try:
            result = _response_info(error.code, error.headers, error.read(READ_CAP))
        finally:
            error.close()
    except (urllib.error.URLError, OSError, TimeoutError):
        result = _transport_error()
    return {
        "schema": "c3-10-groq-network-v1",
        "probe": "unauthenticated_models_metadata_get",
        "request_count": 1,
        "authorization_sent": False,
        "request_body_sent": False,
        "redirects_followed": False,
        "model_inference_requested": False,
        "source_data_sent": False,
        "database_reads": 0,
        "database_writes": 0,
        "publication_authorized": False,
        **result,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args(argv)
    result = probe()
    args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("GROQ_NETWORK_PROBE " + json.dumps({
        key: result[key] for key in (
            "http_status", "content_type_class", "body_prefix_class", "server_class",
            "cf_ray_present", "cf_mitigated_challenge", "transport_error",
        )
    }, sort_keys=True))
    # 401 JSON-like is expected when models endpoint is reached without a key.
    return 0 if result["http_status"] == 401 and result["body_prefix_class"] == "json_like" else 2


if __name__ == "__main__":
    raise SystemExit(main())
