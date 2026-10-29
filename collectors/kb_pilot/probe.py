"""One neutral grounded question through generateContent and through Interactions.

Prints only the structure of each answer (keys, step types, usage fields, whether search ran) so the
pilot can use the request shape that actually searches. No source text, no key.
"""
import json
import os
import urllib.error
import urllib.request

from ..synthesis.live import GEMINI_URL, MODEL

QUESTION = "Which country won UEFA Euro 2024 and on what date was the final played? Use Google Search."
INTERACTIONS_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"


def post(url, key, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"x-goog-api-key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=90) as reply:
            return json.load(reply)
    except urllib.error.HTTPError as exc:
        try:
            return {"_http": exc.code, "_error": json.loads(exc.read().decode())["error"]["message"][:300]}
        except Exception:
            return {"_http": exc.code}


def shape(value, depth=0):
    """Keys and types down to a few levels; strings are replaced by their length."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {k: shape(v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [shape(v, depth + 1) for v in value[:3]] + (["+%d" % (len(value) - 3)] if len(value) > 3 else [])
    if isinstance(value, str):
        return "str(%d)" % len(value)
    return value


def main():
    key = os.environ["GEMINI_API_KEY"]
    variants = {
        "generate_snake": (GEMINI_URL.format(MODEL), {"contents": [{"role": "user", "parts": [{"text": QUESTION}]}],
                                                      "tools": [{"google_search": {}}]}),
        "generate_camel": (GEMINI_URL.format(MODEL), {"contents": [{"role": "user", "parts": [{"text": QUESTION}]}],
                                                      "tools": [{"googleSearch": {}}]}),
        "interactions": (INTERACTIONS_URL, {"model": MODEL, "input": QUESTION, "store": False,
                                            "tools": [{"type": "google_search"}]}),
    }
    for name, (url, body) in variants.items():
        reply = post(url, key, body)
        print("KB_PROBE " + name + " " + json.dumps(shape(reply), ensure_ascii=False)[:6000])
    return 0
