"""C3-10 opt-in Groq authorization probe: ONE benign model call, no private data.

This is not a fact-checker and cannot grant publishing. No database, no source
captured text, no secret or HTTP response body in diagnostics. Manual invocation.
"""
from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "openai/gpt-oss-20b"
# Deliberately low ceiling, including model reasoning. May still use API quota.
MAX_COMPLETION_TOKENS = 256
KNOWN_ERROR_CODES = frozenset({
    "model_permission_blocked_org", "model_permission_blocked_project",
    "model_not_found", "invalid_api_key", "insufficient_permissions",
    "rate_limit_exceeded", "invalid_request_error", "permission_denied",
    "organization_restricted", "project_restricted",
})
KNOWN_ERROR_TYPES = frozenset({
    "permissions_error", "invalid_request_error", "authentication_error",
    "rate_limit_error", "api_error", "permission_error",
})


def benign_payload() -> dict:
    """No user material, retrieved content, or BOHUN domain data in this call."""
    return {
        "model": MODEL,
        "temperature": 0,
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
        "reasoning_effort": "low",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "Return only a small JSON object."},
            {"role": "user", "content": 'Return exactly {"ok":true}.'},
        ],
    }


def _safe_code(raw: bytes) -> tuple[str, str]:
    """Never include raw remote response strings or unrecognized code values."""
    try:
        value = json.loads(raw.decode("utf-8"))
        error = value.get("error", {}) if isinstance(value, dict) else {}
        if not isinstance(error, dict):
            return "missing", "missing"
        code, typ = error.get("code"), error.get("type")
        return (code if isinstance(code, str) and code in KNOWN_ERROR_CODES else "other",
                typ if isinstance(typ, str) and typ in KNOWN_ERROR_TYPES else "other")
    except (ValueError, UnicodeError):
        return "unreadable", "unreadable"


def request_once(key: str, *, opener=None) -> dict:
    """Attempt exactly once. On HTTP failure, decode ONLY allowlisted error fields."""
    if not isinstance(key, str) or not key.strip():
        raise ValueError("missing_groq_key")
    if opener is None:
        opener = urllib.request.urlopen
    body = json.dumps(benign_payload(), separators=(",", ":")).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    })
    try:
        with opener(req, timeout=25) as response:
            # No response content is needed or stored.
            status = response.status
            return {"http_status": status if type(status) is int else None,
                    "request_accepted": status == 200,
                    "error_code": "none" if status == 200 else "unknown_http_response",
                    "error_type": "none" if status == 200 else "unknown_http_response"}
    except urllib.error.HTTPError as exc:
        try:
            code, typ = _safe_code(exc.read(2048))
        finally:
            exc.close()
        return {"http_status": exc.code if type(exc.code) is int else None,
                "request_accepted": False, "error_code": code, "error_type": typ}
    except (urllib.error.URLError, OSError, TimeoutError):
        return {"http_status": None, "request_accepted": False,
                "error_code": "transport_failure", "error_type": "transport_failure"}


def report(key: str, *, opener=None) -> dict:
    result = request_once(key, opener=opener)
    return {
        "schema": "c3-10-groq-access-probe-v1",
        "provider": "groq", "model": MODEL,
        "probe": "single_benign_test_message_no_source_data",
        "attempted_provider_calls": 1,
        "database_reads": 0, "database_writes": 0,
        "user_material_sent": False, "publication_authorized": False,
        **result,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-single-benign-call", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    if not args.allow_single_benign_call:
        raise SystemExit("EXPLICIT_OPT_IN_REQUIRED")
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise SystemExit("GROQ_API_KEY_ABSENT")
    result = report(key)
    args.report.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    # These fields are fixed-size, from a closed allowlist; no messages or secrets.
    print("GROQ_ACCESS_PROBE " + json.dumps({
        "http_status": result["http_status"],
        "error_code": result["error_code"],
        "error_type": result["error_type"],
        "request_accepted": result["request_accepted"],
        "publication_authorized": False,
    }, sort_keys=True))
    return 0 if result["request_accepted"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
