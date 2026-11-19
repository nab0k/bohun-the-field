"""A1SD-02 site judge: a free language model reads a site's home-page headlines and says whether it is a specialised
defence source, what kind of source it is, and whether it has a defence section (owner 19.11: "отлично, давай").

The judge only advises: admission rules still decide. Free models already used by FIELD (collectors/kb_pilot/free.py):
Groq (openai/gpt-oss-120b), Mistral (mistral-small-latest), Gemini (gemini-3.5-flash-lite) - first that answers.
A provider failing twice in a row is switched off for the run. Only headlines, the host and the site name are sent.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

TYPES = ("media", "company_news", "agency", "procurement", "grants", "investment", "company_registry",
         "association", "exhibition", "research")
PROMPT = """You classify a website as a source for a defence-industry news monitor (Europe, Ukraine, NATO).
Site host: {host}
Site name: {name}
Headlines and links from its home page (up to 40):
{lines}

Answer with one JSON object only:
{{"specialised": true|false,   // does the site mostly publish about defence, military, security industry, arms?
  "type": one of {types},
  "defence_section": "<a link from the list that leads to a defence/military/security section or tag>" or null,
  "confidence": number 0..1}}
A general news site, a business or tech portal, or a company that is not mainly in defence is NOT specialised; it may
still have a defence section. Use only links that appear in the list."""


def _providers():
    out = []
    if os.environ.get("GROQ_API_KEY"):
        out.append(("groq", "https://api.groq.com/openai/v1/chat/completions", os.environ["GROQ_API_KEY"],
                    os.environ.get("JUDGE_GROQ_MODEL", "openai/gpt-oss-120b")))
    if os.environ.get("MISTRAL_API_KEY"):
        out.append(("mistral", "https://api.mistral.ai/v1/chat/completions", os.environ["MISTRAL_API_KEY"],
                    os.environ.get("JUDGE_MISTRAL_MODEL", "mistral-small-latest")))
    key = os.environ.get("GEMINI_SEARCH_API_KEY") or os.environ.get("GEMINI_FREE_API_KEY")
    if key:
        out.append(("gemini", None, key, os.environ.get("JUDGE_GEMINI_MODEL", "gemini-3.5-flash-lite")))
    return out


def _post(url, body, headers, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _ask(provider, prompt):
    name, url, key, model = provider
    if name == "gemini":
        data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                     {"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"}},
                     {"x-goog-api-key": key})
        return data["candidates"][0]["content"]["parts"][0]["text"]
    data = _post(url, {"model": model, "temperature": 0.1, "max_tokens": 300,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "user", "content": prompt}]}, {"Authorization": "Bearer " + key})
    return data["choices"][0]["message"]["content"]


def parse(text, links):
    """Model answer -> verdict, or None when it is not usable. A section must be one of the given links."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(v.get("specialised"), bool):
        return None
    t = v.get("type") if v.get("type") in TYPES else None
    section = v.get("defence_section") if v.get("defence_section") in links else None
    try:
        conf = max(0.0, min(1.0, float(v.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    return {"specialised": v["specialised"], "type": t, "defence_section": section, "confidence": round(conf, 2)}


class Judge:
    def __init__(self, providers=None, ask=_ask, pause=2.0):
        self.providers = list(providers if providers is not None else _providers())
        self.ask, self.pause, self.streak, self.off = ask, pause, {}, set()
        self.used, self.errors = {}, {}
        self._last = 0.0

    def __bool__(self):
        return any(p[0] not in self.off for p in self.providers)

    def __call__(self, host, name, pairs):
        """pairs: [(url, label)] from the home page. Returns a verdict with "by" = provider, or None."""
        pairs = [(u, (lbl or "").strip()[:140]) for u, lbl in pairs if u and (lbl or "").strip()][:40]
        if len(pairs) < 5:
            return None
        links = {u for u, _ in pairs}
        prompt = PROMPT.format(host=host, name=(name or "")[:120], types=list(TYPES),
                               lines="\n".join(f"- {lbl} <{u}>" for u, lbl in pairs))
        for p in self.providers:
            if p[0] in self.off:
                continue
            wait = self._last + self.pause - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.used[p[0]] = self.used.get(p[0], 0) + 1
            try:
                verdict = parse(self.ask(p, prompt), links)
            except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError, TypeError) as exc:
                kind = f"{p[0]}:{type(exc).__name__}:{getattr(exc, 'code', '')}"
                self.errors[kind] = self.errors.get(kind, 0) + 1
                self.streak[p[0]] = self.streak.get(p[0], 0) + 1
                if self.streak[p[0]] >= 2:
                    self.off.add(p[0])
                continue
            self.streak[p[0]] = 0
            if verdict:
                return {**verdict, "by": p[0]}
        return None
