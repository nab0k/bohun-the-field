"""One Gemini Free request with a pinned public fixture."""
import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

ENDPOINT = ("https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.5-flash-lite:generateContent")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate(answer, documents):
    rows = answer.get("stages")
    if not isinstance(rows, list) or len(rows) != len(documents):
        raise ValueError("Expected one stage per source")
    expected = {doc["id"]: doc for doc in documents}
    checked = []
    for row in rows:
        doc = expected.pop(row["document_id"])
        evidence = row["evidence"]
        if (row["stage"] != doc["id"] or row["published"] != doc["published"]
                or len(evidence.strip()) < 20 or evidence not in doc["excerpt"]
                or "rheinmetall" not in row["buyer"].casefold()
                or "expal" not in row["target"].casefold()):
            raise ValueError("Stage, entities, date or evidence failed validation")
        checked.append({key: row[key] for key in (
            "stage", "published", "buyer", "target", "document_id", "evidence")})
        checked[-1]["source_url"] = doc["url"]
    return checked


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-response", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = Path(__file__).with_name("control-request.json").read_bytes()
    docs = json.loads(json.loads(payload)["contents"][0]["parts"][0]["text"])["documents"]
    if args.fixture_response:
        answer = json.loads(args.fixture_response.read_text())
        result = {"mode": "fixture", "stages": validate(answer, docs)}
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
                data = json.load(response)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"Gemini Free returned HTTP {exc.code}") from None
        except (OSError, urllib.error.URLError):
            raise SystemExit("Gemini Free transport failed") from None
        parts = data["candidates"][0]["content"]["parts"]
        answer = json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))
        result = {"mode": "live", "stages": validate(answer, docs), "usage": data.get("usageMetadata")}
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
