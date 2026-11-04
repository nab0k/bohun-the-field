"""One Gemini Free request with a pinned public fixture."""
import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.5-flash-lite:generateContent")


MAX_RESPONSE_BYTES = 64 * 1024
MAX_PREVIEW_CHARS = 4096


class InvalidAnswer(ValueError):
    def __init__(self, code, document_id=None):
        self.detail = {"code": code}
        if document_id is not None:
            self.detail["document_id"] = document_id


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate(answer, documents):
    rows = answer.get("stages") if isinstance(answer, dict) else None
    if not isinstance(rows, list) or len(rows) != len(documents):
        raise InvalidAnswer("stage_count")
    expected = {doc["id"]: doc for doc in documents}
    checked = []
    for row in rows:
        document_id = row.get("document_id") if isinstance(row, dict) else None
        if not isinstance(document_id, str) or document_id not in expected:
            raise InvalidAnswer("document_id")
        doc = expected.pop(document_id)
        if row.get("stage") != document_id:
            raise InvalidAnswer("stage", document_id)
        if row.get("published") != doc["published"]:
            raise InvalidAnswer("published", document_id)
        for field, name in (("buyer", "rheinmetall"), ("target", "expal")):
            value = row.get(field)
            if not isinstance(value, str) or name not in value.casefold():
                raise InvalidAnswer(field, document_id)
        evidence = row.get("evidence")
        if (not isinstance(evidence, str) or len(evidence.strip()) < 20
                or evidence not in doc["excerpt"]):
            raise InvalidAnswer("evidence", document_id)
        checked.append({key: row[key] for key in (
            "stage", "published", "buyer", "target", "document_id", "evidence")})
        checked[-1]["source_url"] = doc["url"]
    return checked


def check_response(response_text, docs, mode, output, key="", usage=None):
    result = {"mode": mode}
    try:
        result["stages"] = validate(json.loads(response_text), docs)
    except json.JSONDecodeError:
        result["error"] = {"code": "answer_json"}
    except InvalidAnswer as exc:
        result["error"] = exc.detail
    if "error" in result:
        if key:
            response_text = response_text.replace(key, "[REDACTED]")
        result["response_preview"] = response_text[:MAX_PREVIEW_CHARS]
        result["response_truncated"] = len(response_text) > MAX_PREVIEW_CHARS
    if usage is not None:
        result["usage"] = usage
    serialized = json.dumps(result, indent=2)
    if key:
        serialized = serialized.replace(key, "[REDACTED]")
    output.write_text(serialized + "\n", encoding="utf-8")
    return "error" not in result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-response", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = Path(__file__).with_name("control-request.json").read_bytes()
    docs = json.loads(json.loads(payload)["contents"][0]["parts"][0]["text"])["documents"]
    if args.fixture_response:
        response_text = args.fixture_response.read_text(encoding="utf-8")
        valid = check_response(response_text, docs, "fixture", args.output)
    else:
        key = os.environ.get("GEMINI_FREE_API_KEY", "").strip()
        if not key:
            raise SystemExit("Gemini Free key unavailable")
        request = urllib.request.Request(
            ENDPOINT, data=payload, method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=45) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"Gemini Free returned HTTP {exc.code}") from None
        except (OSError, urllib.error.URLError):
            raise SystemExit("Gemini Free transport failed") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise SystemExit("Gemini Free response exceeded limit")
        try:
            data = json.loads(raw)
            parts = data["candidates"][0]["content"]["parts"]
            response_text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise SystemExit("Gemini Free response envelope invalid") from None
        valid = check_response(response_text, docs, "live", args.output, key, data.get("usageMetadata"))
    if not valid:
        raise SystemExit("Gemini Free answer rejected; see control result artifact")


if __name__ == "__main__":
    main()
