"""C3-10 one-shot, OWNER-OPERATED Groq blind-P2 trial (PRIVATE inputs only).

Default mode validates a local staged JSON without network access, secrets or
writing files. The explicit --send path is disabled unless several independent
manual approvals are present. Never use inside GitHub Actions, copy private
inputs into the repository, or treat predictions as publication permission.

Example OFFLINE preflight:
  python -m collectors.editor.manual_groq_trial --staged /private/staged.json \
    --expected-sha256 <exact verified raw staged-file SHA-256>

Execution requires a SEPARATE owner decision; see --help for extra safeguards.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import ssl
import sys
import urllib.error
import urllib.request

SCHEMA = "bohun-c3-10-private-staged-groq-bodies-v1"
MODEL = "openai/gpt-oss-20b"
ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
MATERIALS = frozenset({"syn-483", "syn-488", "syn-495", "syn-501", "syn-502"})
STATUSES = frozenset({"supported", "contradicted", "insufficient"})
MAX_BATCHES = 5
MAX_CLAIMS = 25
MAX_SPANS = 17
MAX_BYTES = 12000
MAX_RESPONSE_BYTES = 30000
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _bytes(value, *, canonical=False) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=canonical,
                      separators=(",", ":") if canonical else None,
                      allow_nan=False).encode("utf-8")


def check_staged(raw: bytes, expected_sha256: str) -> tuple[dict, list[dict], dict]:
    """Validate the exact staged payload, including privacy-relevant boundaries."""
    if not isinstance(expected_sha256, str) or not _SHA.fullmatch(expected_sha256):
        raise ValueError("invalid_expected_stage_sha256")
    if sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("staged_file_integrity_mismatch")
    if len(raw) > 100_000:
        raise ValueError("oversized_staged_file")
    try:
        staged = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid_staged_json") from None
    if not isinstance(staged, dict) or set(staged) != {
        "schema", "status", "holdout_sha256", "model_calls", "max_request_bytes",
        "owner_authorization_required_before_network", "prediction_mapping", "requests",
    }:
        raise ValueError("invalid_staged_schema")
    if (staged["schema"] != SCHEMA or staged["status"] != "PREPARED_OFFLINE_NOT_SENT"
            or type(staged["model_calls"]) is not int or staged["model_calls"] != 0
            or staged["owner_authorization_required_before_network"] is not True
            or type(staged["max_request_bytes"]) is not int
            or staged["max_request_bytes"] != MAX_BYTES
            or not isinstance(staged["holdout_sha256"], str)
            or not _SHA.fullmatch(staged["holdout_sha256"])
            or not isinstance(staged["prediction_mapping"], str)):
        raise ValueError("invalid_staged_metadata")
    batches = staged["requests"]
    if not isinstance(batches, list) or len(batches) != MAX_BATCHES:
        raise ValueError("invalid_batch_count")
    seen_materials: set[str] = set()
    seen_claims: set[str] = set()
    seen_spans: set[str] = set()
    manifests = []
    for batch in batches:
        if not isinstance(batch, dict) or set(batch) != {
            "material_id", "claim_count", "span_count", "body_sha256",
            "request_bytes_utf8", "request_body",
        }:
            raise ValueError("invalid_batch")
        mid, body = batch["material_id"], batch["request_body"]
        if (type(mid) is not str or mid not in MATERIALS or mid in seen_materials
                or not isinstance(body, dict) or set(body) != {
                    "model", "temperature", "max_completion_tokens",
                    "response_format", "messages",
                }):
            raise ValueError("invalid_batch")
        seen_materials.add(mid)
        if (body["model"] != MODEL or body["temperature"] != 0
                or body["max_completion_tokens"] != 1600
                or body["response_format"] != {"type": "json_object"}
                or not isinstance(body["messages"], list)
                or len(body["messages"]) != 2):
            raise ValueError("unsafe_provider_request")
        system, message = body["messages"]
        if (not isinstance(system, dict) or set(system) != {"role", "content"}
                or system["role"] != "system" or not isinstance(system["content"], str)
                or not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] != "user" or not isinstance(message["content"], str)
                or "only supplied p2" not in system["content"].casefold()
                or "return valid json" not in system["content"].casefold()):
            raise ValueError("unsafe_provider_messages")
        try:
            user = json.loads(message["content"])
        except ValueError:
            raise ValueError("invalid_private_user_payload") from None
        if not isinstance(user, dict) or set(user) != {
            "material_id", "version", "claims", "spans",
            "publication_authorized", "source_class",
        }:
            raise ValueError("invalid_private_user_payload")
        if (user["material_id"] != mid or user["publication_authorized"] is not False
                or user["source_class"] != "unverified P2"
                or type(user["version"]) is not int
                or not isinstance(user["claims"], list)
                or not isinstance(user["spans"], list)
                or len(user["claims"]) != 5 or not 1 <= len(user["spans"]) <= 4):
            raise ValueError("invalid_private_user_payload")
        local_claims = set()
        local_evidence = set()
        for claim in user["claims"]:
            if (not isinstance(claim, dict) or set(claim) != {"claim_id", "text"}
                    or type(claim["claim_id"]) is not str
                    or not claim["claim_id"].startswith("c310-new-" + mid[4:] + "-")
                    or claim["claim_id"] in seen_claims
                    or not isinstance(claim["text"], str)
                    or not 1 <= len(claim["text"]) <= 3000):
                raise ValueError("invalid_private_claim")
            seen_claims.add(claim["claim_id"])
            local_claims.add(claim["claim_id"])
        for span in user["spans"]:
            if (not isinstance(span, dict)
                    or set(span) != {"source_id", "span_id", "quote", "url"}
                    or type(span["source_id"]) is not str or not span["source_id"]
                    or type(span["span_id"]) is not str or not span["span_id"]
                    or span["span_id"] in seen_spans
                    or not isinstance(span["quote"], str)
                    or not 1 <= len(span["quote"]) <= 200
                    or type(span["url"]) is not str
                    or not span["url"].startswith("https://")):
                raise ValueError("invalid_private_span")
            seen_spans.add(span["span_id"])
            local_evidence.add((span["source_id"], span["span_id"]))
        raw_body = _bytes(body)
        if (type(batch["claim_count"]) is not int or batch["claim_count"] != 5
                or type(batch["span_count"]) is not int
                or batch["span_count"] != len(user["spans"])
                or type(batch["request_bytes_utf8"]) is not int
                or batch["request_bytes_utf8"] != len(raw_body)
                or len(raw_body) > MAX_BYTES
                or type(batch["body_sha256"]) is not str
                or batch["body_sha256"] != sha256(_bytes(body, canonical=True)).hexdigest()):
            raise ValueError("staged_batch_integrity_mismatch")
        manifests.append({
            "material_id": mid, "request_body": body,
            "claim_ids": local_claims, "valid_evidence": local_evidence,
            "size": len(raw_body),
        })
    if (seen_materials != MATERIALS or len(seen_claims) != MAX_CLAIMS
            or len(seen_spans) != MAX_SPANS):
        raise ValueError("staged_total_scope_mismatch")
    return staged, manifests, {
        "schema": "bohun-c3-10-manual-local-preflight-v1",
        "status": "PREPARED_OFFLINE_NOT_SENT",
        "material_count": len(seen_materials), "claim_count": len(seen_claims),
        "span_count": len(seen_spans), "requests_max": MAX_BATCHES,
        "max_request_bytes": max(m["size"] for m in manifests),
        "publication_authorized": False, "provider_calls": 0,
        "labels_sent": False,
    }


def normalize_checks(material: dict, response: dict) -> dict:
    """Keep only well-formed claim/status/linked-span IDs, never raw LLM text."""
    if not isinstance(response, dict) or "checks" not in response:
        raise ValueError("invalid_provider_output")
    checks = response["checks"]
    if not isinstance(checks, list) or len(checks) != 5:
        raise ValueError("incomplete_provider_checks")
    seen = set()
    out = []
    for row in checks:
        if (not isinstance(row, dict) or set(row) != {
                "claim_id", "status", "source_id", "span_id"}):
            raise ValueError("invalid_provider_check")
        cid, status = row["claim_id"], row["status"]
        if (type(cid) is not str or cid not in material["claim_ids"] or cid in seen
                or type(status) is not str or status not in STATUSES):
            raise ValueError("invalid_provider_check")
        seen.add(cid)
        src, span = row["source_id"], row["span_id"]
        if status == "insufficient":
            if src is not None or span is not None:
                raise ValueError("invalid_insufficient_citation")
        elif (type(src) is not str or type(span) is not str
                or (src, span) not in material["valid_evidence"]):
            raise ValueError("invalid_provider_citation")
        out.append({
            "claim_id": cid, "status": status, "source_id": src, "span_id": span,
        })
    return {"material_id": material["material_id"], "checks": out}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _real_transport(body: dict, api_key: str) -> dict:
    """Only external IO: fixed HTTPS host, no proxy, redirects or retries."""
    request = urllib.request.Request(
        ENDPOINT, data=_bytes(body), method="POST",
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json",
                 "User-Agent": "BOHUN-C3-10-private-manual/1.0"},
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirect(),
    )
    try:
        with opener.open(request, timeout=30) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, urllib.error.URLError, ValueError):
        raise ValueError("provider_transport_failed") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError("provider_response_oversized")
    try:
        wrapped = json.loads(data)
        if (not isinstance(wrapped, dict) or wrapped.get("model") != MODEL
                or not isinstance(wrapped.get("choices"), list)
                or len(wrapped["choices"]) != 1
                or wrapped["choices"][0].get("finish_reason") != "stop"):
            raise ValueError("provider_identity_or_completion_failed")
        text = wrapped["choices"][0]["message"]["content"]
        if not isinstance(text, str) or len(text) > 25000:
            raise ValueError("provider_response_invalid")
        answer = json.loads(text)
        if not isinstance(answer, dict):
            raise ValueError("provider_response_invalid")
        return answer
    except (KeyError, TypeError, json.JSONDecodeError, IndexError):
        raise ValueError("provider_response_invalid") from None


def _append_marker(fd: int, record: dict) -> None:
    os.write(fd, _bytes(record) + b"\n")
    os.fsync(fd)


def run_once(manifests: list[dict], *, key: str, state_path: Path,
             output_path: Path, transport=None) -> dict:
    """No reruns: exclusive private marker is created BEFORE first network call.

    Failure leaves the marker in place. No retry, no batch skipping, no
    model fallback. The private result contains ID/status only, not texts.
    """
    if not isinstance(key, str) or not key.strip():
        raise ValueError("provider_key_absent")
    if state_path.resolve() == output_path.resolve():
        raise ValueError("state_and_output_collide")
    if state_path.exists() or output_path.exists():
        raise ValueError("trial_already_attempted_or_output_exists")
    transport = transport or _real_transport
    fd = os.open(str(state_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _append_marker(fd, {"event": "trial_reserved", "max_attempts": MAX_BATCHES})
        responses = []
        for material in manifests:
            _append_marker(fd, {
                "event": "attempt_reserved", "material_id": material["material_id"],
            })
            reply = transport(material["request_body"], key)
            parsed = normalize_checks(material, reply)
            responses.append(parsed)
            _append_marker(fd, {
                "event": "response_validated", "material_id": material["material_id"],
            })
        result = {"responses": responses}
        output_fd = os.open(str(output_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(output_fd, "w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        _append_marker(fd, {"event": "completed", "responses": len(responses)})
        return {"status": "PRIVATE_RESULTS_SAVED", "response_batches": len(responses),
                "claims_count": MAX_CLAIMS, "publication_authorized": False}
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--send", action="store_true", help="disabled without separate owner authorization")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--owner-approval", help="must be EXTERNAL_P2_5_CALLS_APPROVED")
    parser.add_argument("--zero-billing-confirmed", action="store_true")
    args = parser.parse_args(argv)
    try:
        raw = args.staged.read_bytes()
        staged, manifests, report = check_staged(raw, args.expected_sha256)
        if not args.send:
            print(json.dumps(report, sort_keys=True))
            return 0
        if (args.owner_approval != "EXTERNAL_P2_5_CALLS_APPROVED"
                or os.environ.get("BOHUN_C310_ALLOW_EXTERNAL") != "YES"
                or not args.zero_billing_confirmed):
            raise ValueError("separate_owner_approval_and_zero_billing_required")
        if not args.output or not args.state:
            raise ValueError("private_output_and_state_required")
        if os.name != "nt" and args.staged.stat().st_mode & 0o077:
            raise ValueError("staged_file_must_be_private_0600")
        if args.staged.is_symlink():
            raise ValueError("refuse_staged_symlink")
        if args.output.resolve() in {args.staged.resolve(), args.state.resolve()}:
            raise ValueError("refuse_overwrite_private_input")
        if args.state.resolve() == args.staged.resolve():
            raise ValueError("refuse_overwrite_private_input")
        answer = run_once(manifests, key=os.environ.get("GROQ_API_KEY", ""),
                          state_path=args.state, output_path=args.output)
        print(json.dumps(answer, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError) as ex:
        # Do not expose provider messages, claim bodies or tokens in stderr.
        code = str(ex)
        allowed = re.fullmatch(r"[a-z][a-z0-9_]{0,90}", code)
        print("C3-10 stopped: " + (code if allowed else "private_trial_failed"),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
