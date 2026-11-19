"""A1SD-02 site judge: a free language model reads a site's home-page headlines and says whether it is a specialised
defence source, what kind of source it is, and whether it has a defence section (owner 19.11: "отлично, давай").

The judge only advises: admission rules still decide. Free models already used by FIELD (collectors/kb_pilot/free.py):
Groq (openai/gpt-oss-120b), Mistral (mistral-small-latest), Gemini (gemini-3.5-flash-lite) - first that answers.
A provider failing twice in a row is switched off for the run. Only headlines, the host and the site name are sent.

Cloud run 4 (19.11): Groq answered 403 to every call - its front blocks the default Python User-Agent (error 1010,
as collectors/kb_pilot/free.py notes), so a named agent is sent. Mistral answered 429 twice and was switched off: a
rate limit is a reason to wait and pass the site on, not to drop the provider; 401/403 still switch it off at once.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

USER_AGENT = "bohun-source-search/1.0 (+https://github.com/nab0k/bohun-the-field)"
OFF_AFTER_RATE_LIMITS = 5      # consecutive 429s that switch a provider off for the run
# seconds between calls to one provider (free per-minute limits); manual run 19.11: Groq answered 429 after 26 calls
# 2 s apart, so 4 s
PACE = {"groq": 4.0, "mistral": 3.0, "gemini": 2.0}
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
A general news site, a business or tech portal, a job or recruitment board, a shop, or a company that is not mainly in
defence is NOT specialised; it may still have a defence section. Use only links that appear in the list."""


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
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers})
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
    # openai/gpt-oss-120b reasons first; a small cap cut the answer before the JSON (kb_pilot uses 8 000)
    data = _post(url, {"model": model, "temperature": 0.1, "max_tokens": 2000,
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
    def __init__(self, providers=None, ask=_ask, pause=None):
        self.providers = list(providers if providers is not None else _providers())
        self.ask, self.pause, self.streak, self.limited, self.off = ask, pause, {}, {}, set()
        self.used, self.errors = {}, {}
        self._last = {}

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
            gap = self.pause if self.pause is not None else PACE.get(p[0], 2.0)
            wait = self._last.get(p[0], 0.0) + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last[p[0]] = time.time()
            self.used[p[0]] = self.used.get(p[0], 0) + 1
            try:
                verdict = parse(self.ask(p, prompt), links)
            except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError, TypeError) as exc:
                code = getattr(exc, "code", "")
                kind = f"{p[0]}:{type(exc).__name__}:{code}"
                self.errors[kind] = self.errors.get(kind, 0) + 1
                if code == 429:
                    self.limited[p[0]] = self.limited.get(p[0], 0) + 1
                    if self.limited[p[0]] >= OFF_AFTER_RATE_LIMITS:
                        self.off.add(p[0])
                    continue
                if code in (401, 403):
                    self.off.add(p[0])
                    continue
                self.streak[p[0]] = self.streak.get(p[0], 0) + 1
                if self.streak[p[0]] >= 2:
                    self.off.add(p[0])
                continue
            self.streak[p[0]] = 0
            self.limited[p[0]] = 0
            if verdict:
                return {**verdict, "by": p[0]}
        return None
