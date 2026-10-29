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


def cloudflare():
    """Status codes and Cloudflare error codes only — never the token or account id."""
    import re
    acc = (os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "")
    tok = (os.environ.get("CLOUDFLARE_WORKERS_AI_TOKEN") or "")
    facts = {"account_len": len(acc), "account_hex32": bool(re.fullmatch(r"[0-9a-f]{32}", acc.strip())),
             "account_has_space": acc != acc.strip(), "token_len": len(tok), "token_has_space": tok != tok.strip()}
    print("KB_CF_SHAPE " + json.dumps(facts))
    acc, tok = acc.strip(), tok.strip()
    head = {"Authorization": "Bearer " + tok}

    def call(name, url, body=None):
        req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                     method="POST" if body else "GET",
                                     headers={**head, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r); code = r.status
        except urllib.error.HTTPError as exc:
            code = exc.code
            try:
                data = json.loads(exc.read().decode())
            except Exception:
                data = {}
        except Exception as exc:
            print("KB_CF %s transport %s" % (name, type(exc).__name__)); return
        errs = [{"code": e.get("code"), "message": str(e.get("message"))[:120]} for e in data.get("errors") or []]
        status = (data.get("result") or {}).get("status") if isinstance(data.get("result"), dict) else None
        print("KB_CF %s http=%s success=%s status=%s errors=%s" % (name, code, data.get("success"), status, json.dumps(errs)))

    call("user_token_verify", "https://api.cloudflare.com/client/v4/user/tokens/verify")
    call("account_token_verify", "https://api.cloudflare.com/client/v4/accounts/%s/tokens/verify" % acc)
    call("ai_chat", "https://api.cloudflare.com/client/v4/accounts/%s/ai/v1/chat/completions" % acc,
         {"model": "@cf/google/gemma-4-26b-a4b-it", "max_tokens": 5, "messages": [{"role": "user", "content": "Say OK"}]})
    # How each free model returns JSON: message keys, finish reason, content length/prefix, parse result, time.
    import time
    task = [{"role": "system", "content": "Return only one JSON object: {\"items\": [{\"name\": string, \"year\": integer}]} "
                                          "listing three NATO members with their joining year. No prose."},
            {"role": "user", "content": "Go."}]
    for model in ("@cf/google/gemma-4-26b-a4b-it", "@cf/zai-org/glm-4.7-flash", "@cf/nvidia/nemotron-3-120b-a12b"):
        for fmt in (None, {"type": "json_object"}):
            body = {"model": model, "max_tokens": 600, "temperature": 0.2, "messages": task}
            if fmt:
                body["response_format"] = fmt
            req = urllib.request.Request("https://api.cloudflare.com/client/v4/accounts/%s/ai/v1/chat/completions" % acc,
                                         data=json.dumps(body).encode(), method="POST",
                                         headers={**head, "Content-Type": "application/json"})
            t0 = time.time()
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r); code = r.status
            except urllib.error.HTTPError as exc:
                code, data = exc.code, {}
                try:
                    data = json.loads(exc.read().decode())
                except Exception:
                    pass
            except Exception as exc:
                print("KB_CF_MODEL %s fmt=%s transport=%s secs=%.0f" % (model, bool(fmt), type(exc).__name__, time.time() - t0))
                continue
            msg = ((data.get("choices") or [{}])[0].get("message") or {})
            content = msg.get("content") or ""
            try:
                from .gemini import parse_json_text
                parsed = type(parse_json_text(content)).__name__
            except Exception as exc:
                parsed = "fail:" + type(exc).__name__
            print("KB_CF_MODEL %s fmt=%s http=%s secs=%.0f keys=%s finish=%s content_len=%d prefix=%s parsed=%s usage=%s errors=%s" % (
                model, bool(fmt), code, time.time() - t0, sorted(msg), (data.get("choices") or [{}])[0].get("finish_reason"),
                len(content), json.dumps(content[:60]), parsed, json.dumps(data.get("usage")),
                json.dumps([e.get("message") for e in data.get("errors") or []])[:200]))
    return 0


FREE_PROVIDERS = {
    # name: (env key, models url, chat url, candidate models, auth style)
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1/models", "https://api.mistral.ai/v1/chat/completions",
                ["mistral-small-latest", "mistral-medium-latest"], "bearer"),
    "groq": ("GROQ_API_KEY", "https://api.groq.com/openai/v1/models", "https://api.groq.com/openai/v1/chat/completions",
             ["openai/gpt-oss-120b", "llama-3.3-70b-versatile"], "bearer"),
    "gemini_free": ("GEMINI_FREE_API_KEY", "https://generativelanguage.googleapis.com/v1beta/models", None,
                    ["gemini-3.5-flash-lite", "gemini-3.8-flash"], "google"),
}


def free_providers():
    """For each free provider: key present, model ids available, one tiny JSON call per candidate model.
    Prints codes, model names, timing and parse results only — never a key."""
    import time
    from .gemini import parse_json_text
    task = "Return only one JSON object: {\"items\": [{\"name\": string, \"year\": integer}]} listing three NATO members with their joining year."
    for name, (env, models_url, chat_url, models, style) in FREE_PROVIDERS.items():
        key = (os.environ.get(env) or "").strip()
        if not key:
            print("KB_FREE %s key=absent" % name); continue
        head = {"Authorization": "Bearer " + key} if style == "bearer" else {"x-goog-api-key": key}
        try:
            req = urllib.request.Request(models_url, headers=head)
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            ids = sorted((m.get("id") or m.get("name") or "") for m in (data.get("data") or data.get("models") or []))
            print("KB_FREE %s models=%d %s" % (name, len(ids), json.dumps([i for i in ids if i][:60])))
        except urllib.error.HTTPError as exc:
            print("KB_FREE %s models_http=%d" % (name, exc.code))
        for model in models:
            if style == "bearer":
                url, body = chat_url, {"model": model, "max_tokens": 400, "temperature": 0.2,
                                       "response_format": {"type": "json_object"},
                                       "messages": [{"role": "user", "content": task}]}
            else:
                url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % model
                body = {"contents": [{"role": "user", "parts": [{"text": task}]}],
                        "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 400}}
            t0 = time.time()
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={**head, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r); code = r.status
            except urllib.error.HTTPError as exc:
                code = exc.code
                try:
                    err = json.loads(exc.read().decode())
                    msg = (err.get("error") or {}).get("message") if isinstance(err.get("error"), dict) else err.get("message") or err.get("error")
                except Exception:
                    msg = ""
                print("KB_FREE %s %s http=%d msg=%s" % (name, model, code, json.dumps(str(msg)[:160]))); continue
            except Exception as exc:
                print("KB_FREE %s %s transport=%s" % (name, model, type(exc).__name__)); continue
            if style == "bearer":
                text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                usage = data.get("usage")
            else:
                parts = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
                text = "".join(p.get("text", "") for p in parts)
                usage = data.get("usageMetadata")
            try:
                parsed = type(parse_json_text(text)).__name__
            except Exception as exc:
                parsed = "fail:" + type(exc).__name__
            print("KB_FREE %s %s http=%d secs=%.1f parsed=%s usage=%s" % (name, model, code, time.time() - t0, parsed,
                                                                          json.dumps(usage)[:200]))
    return 0
