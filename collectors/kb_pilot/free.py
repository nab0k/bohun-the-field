"""Free models for the steps without search (D-20261029-EXP028-CHEAP-SCHEME, A13: all free providers): a router
over free providers in order — Gemini free tier, Cloudflare Workers AI, Mistral, Groq — and paid Gemini as the last
fallback for every request none of them answered. Same interface as gemini.Gemini (request / __call__ / many).

Keys come from the environment only; a provider without a key is skipped. A provider that answers 401/403 is
switched off for the run; 429 and 5xx move the request to the next provider. Answers missing required fields count
as failures (run 40938142142).
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import time
import urllib.error
import urllib.request

from ..synthesis.contracts import canonical
from ..synthesis.live import GEMINI_URL
from .gemini import parse_json_text, parse_reply, unwrap

WORKERS_AI_URL = "https://api.cloudflare.com/client/v4/accounts/{}/ai/v1/chat/completions"
FREE_MODEL = os.environ.get("KB_FREE_MODEL", "@cf/google/gemma-4-26b-a4b-it")
GEMINI_FREE_MODEL = os.environ.get("KB_GEMINI_FREE_MODEL", "gemini-3.5-flash-lite")
# Neurons per million tokens for gemma-4-26b-a4b-it ($0.10 / $0.30 per M at $0.011 per 1 000 neurons).
NEURONS_PER_M_IN, NEURONS_PER_M_OUT = 9_091, 27_273
FREE_NEURONS_PER_RUN = 9_000      # stay under the 10 000 free neurons per day
WORKERS = 3
MIN_OUTPUT_TOKENS = 8000          # free models may reason before answering; a small cap cuts the JSON
REQUIRED_NONEMPTY = {"what_happened", "groups"}
OFF_AFTER_RATE_LIMITS = 5         # consecutive 429s that switch a provider off for the run
# Minimum seconds between calls per provider (free tiers count requests per minute; run 40947456510: Gemini free
# answered 20 requests, then 429 with three parallel callers).
MIN_INTERVAL = {"gemini_free": float(os.environ.get("KB_GEMINI_FREE_INTERVAL", "4.5")), "groq": 2.0}


USER_AGENT = "bohun-kb-pilot/1.0 (+https://bohundefence.com)"  # Groq answers 403 to the default Python agent


def http_post_json(url, headers, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**headers, "Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as reply:
        return json.load(reply)


def unwrap_schema_echo(answer, schema):
    """A model sometimes returns {"properties": {...}} (the schema's shape) instead of the object (run 40939444092)."""
    required = (schema or {}).get("required", [])
    if isinstance(answer, dict) and isinstance(answer.get("properties"), dict) and required \
            and not any(k in answer for k in required) and any(k in answer["properties"] for k in required):
        return answer["properties"]
    return answer


def check_answer(answer, schema):
    answer = unwrap_schema_echo(answer, schema)
    if not isinstance(answer, dict):
        raise RuntimeError("not_object")
    missing = [k for k in (schema or {}).get("required", [])
               if k not in answer or (isinstance(answer[k], str) and not answer[k].strip())
               or (k in REQUIRED_NONEMPTY and not answer[k])]
    if missing:
        raise RuntimeError("incomplete")
    return answer


class OpenAICompatible:
    """Workers AI, Mistral, Groq: OpenAI chat completions."""

    def __init__(self, name, url, key, model, extra=None, neuron_cap=None):
        self.name, self.url, self.key, self.model, self.extra = name, url, key, model, extra or {}
        self.neuron_cap, self.neurons = neuron_cap, 0

    def call(self, req, transport):
        if self.neuron_cap is not None:
            est = (req["chars"] // 3) * NEURONS_PER_M_IN // 1_000_000 + 1
            if self.neurons + est > self.neuron_cap:
                raise RuntimeError("allocation")
        body = {"model": self.model, "temperature": 0.2, "max_tokens": req["max_tokens"],
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": req["system"]}, {"role": "user", "content": req["user"]}],
                **self.extra}
        reply = transport(self.url, {"Authorization": "Bearer " + self.key}, body)
        usage = reply.get("usage") or {}
        if self.neuron_cap is not None:
            self.neurons += (int(usage.get("prompt_tokens") or 0) * NEURONS_PER_M_IN
                             + int(usage.get("completion_tokens") or 0) * NEURONS_PER_M_OUT) // 1_000_000 + 1
        choice = (reply.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("truncated")
        text = (choice.get("message") or {}).get("content") or ""
        try:
            return unwrap(parse_json_text(text)), usage
        except Exception:
            raise RuntimeError("bad_json") from None


class GeminiFree:
    """Gemini free tier (separate project without billing): the same request body as paid Gemini, no cost."""

    def __init__(self, key, model=GEMINI_FREE_MODEL):
        self.name, self.key, self.model = "gemini_free", key, model

    def call(self, req, transport):
        reply = transport(GEMINI_URL.format(self.model), {"x-goog-api-key": self.key}, req["gemini"]["body"])
        try:
            answer, _ = parse_reply(reply, req["gemini"]["plain"])
        except Exception:
            raise RuntimeError("bad_json") from None
        return answer, reply.get("usageMetadata")


def cloudflare(account, token, model=FREE_MODEL):
    # Reasoning off (Workers AI docs): with it on, large steps ran out of tokens (run 40935846507).
    return OpenAICompatible("cloudflare", WORKERS_AI_URL.format(account), token, model,
                            {"chat_template_kwargs": {"enable_thinking": False}}, FREE_NEURONS_PER_RUN)


def providers_from_env():
    env = lambda k: (os.environ.get(k) or "").strip()
    out = []
    if env("GEMINI_FREE_API_KEY"):
        out.append(GeminiFree(env("GEMINI_FREE_API_KEY")))
    if env("CLOUDFLARE_ACCOUNT_ID") and env("CLOUDFLARE_WORKERS_AI_TOKEN"):
        out.append(cloudflare(env("CLOUDFLARE_ACCOUNT_ID"), env("CLOUDFLARE_WORKERS_AI_TOKEN")))
    if env("MISTRAL_API_KEY"):
        out.append(OpenAICompatible("mistral", "https://api.mistral.ai/v1/chat/completions", env("MISTRAL_API_KEY"),
                                    env("KB_MISTRAL_MODEL") or "mistral-small-latest"))
    if env("GROQ_API_KEY"):
        out.append(OpenAICompatible("groq", "https://api.groq.com/openai/v1/chat/completions", env("GROQ_API_KEY"),
                                    env("KB_GROQ_MODEL") or "openai/gpt-oss-120b"))
    return out


class FreeModel:
    def __init__(self, fallback, account=None, token=None, transport=http_post_json, model=FREE_MODEL, providers=None):
        self.fallback, self.transport = fallback, transport
        if providers is None:
            if account is not None or token is not None:  # explicit Cloudflare only (tests, single-provider runs)
                providers = [cloudflare(account, token, model)] if account and token else []
            else:
                providers = providers_from_env()
        self.providers = providers
        self.ledger = fallback.ledger
        self.batches = fallback.batches
        self.off, self.rate_limited, self.next_slot = set(), {}, {}
        self.lock = threading.Lock()
        self.stats = {"free_ok": 0, "by_provider": {}, "fallback": {}}

    @property
    def enabled(self):
        return any(p.name not in self.off for p in self.providers)

    def request(self, system, packet, schema, grounded=False, max_output=6000, schema_mode="schema", thinking=None,
                task=None, step="other"):
        if grounded or schema_mode == "plain":
            raise ValueError("FREE_MODEL_HAS_NO_SEARCH")  # search and free text stay on Gemini
        gem = self.fallback.request(system, packet, schema, False, max_output, schema_mode, thinking, task, step)
        user = ((task + "\n\n") if task else "") + canonical(packet)
        return {"gemini": gem, "step": step, "schema": schema, "user": user, "chars": len(user) + len(system),
                "max_tokens": max(max_output, MIN_OUTPUT_TOKENS),
                "system": system + "\n\nReturn only the JSON object itself (not a schema), matching:\n"
                          + canonical(schema)}

    def __call__(self, *args, **kwargs):
        out = self.many([self.request(*args, **kwargs)])[0]
        if out[0] == "error":
            raise out[1]
        return out[1], out[2]

    def _note(self, key, n=1):
        with self.lock:
            self.stats["fallback"][key] = self.stats["fallback"].get(key, 0) + n

    def _pace(self, name):
        gap = MIN_INTERVAL.get(name)
        if not gap:
            return
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_slot.get(name, 0.0))
            self.next_slot[name] = start + gap
        if start > now:
            time.sleep(start - now)

    def _free(self, req, exclude=()):
        for p in self.providers:
            if p.name in self.off or p.name in exclude:
                continue
            self._pace(p.name)
            try:
                answer, usage = p.call(req, self.transport)
                answer = check_answer(answer, req["schema"])
            except urllib.error.HTTPError as exc:
                self._note("%s_http_%d" % (p.name, exc.code))
                with self.lock:
                    if exc.code in (401, 403):
                        self.off.add(p.name)
                    elif exc.code == 429:
                        self.rate_limited[p.name] = self.rate_limited.get(p.name, 0) + 1
                        if self.rate_limited[p.name] >= OFF_AFTER_RATE_LIMITS:
                            self.off.add(p.name)
                continue
            except RuntimeError as exc:
                self._note("%s_%s" % (p.name, exc))
                if str(exc) == "allocation":
                    with self.lock:
                        self.off.add(p.name)
                continue
            except Exception as exc:
                self._note("%s_transport_%s" % (p.name, type(exc).__name__))
                continue
            with self.lock:
                self.rate_limited[p.name] = 0
                self.stats["free_ok"] += 1
                self.stats["by_provider"][p.name] = self.stats["by_provider"].get(p.name, 0) + 1
                self.ledger.steps["free_" + req["step"]] = self.ledger.steps.get("free_" + req["step"], 0)
            return answer, {"cost_micro_usd": 0, "search_queries": 0, "supports": [], "grounding_chunks": [],
                            "model": p.name, "free_tokens": usage}
        raise RuntimeError("no_free_provider")

    def free_many(self, reqs, exclude=None):
        """Free providers only, never paid Gemini (extraction, D-20261030-EXP028-V2-STEP3): ("ok", answer, info) or
        ("error", exception) per request. exclude[i]: provider names request i must not go to (a second opinion)."""
        out = [None] * len(reqs)

        def attempt(i):
            try:
                out[i] = ("ok",) + self._free(reqs[i], exclude[i] if exclude else ())
            except Exception as exc:
                out[i] = ("error", exc)
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            list(pool.map(attempt, range(len(reqs))))
        return out

    def many(self, reqs):
        """Free providers first (in parallel); what none answered goes to paid Gemini together (batch if on)."""
        if not reqs:
            return []
        out = [None] * len(reqs)

        def attempt(i):
            try:
                out[i] = ("ok",) + self._free(reqs[i])
            except RuntimeError:
                pass
        if self.enabled:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                list(pool.map(attempt, range(len(reqs))))
        else:
            self._note("free_disabled", len(reqs))
        rest = [i for i, o in enumerate(out) if o is None]
        if rest:
            self._note("paid_gemini", len(rest))
            answers = self.fallback.many([reqs[i]["gemini"] for i in rest])
            for i, a in zip(rest, answers):
                if a[0] == "ok":
                    try:
                        a = ("ok", check_answer(a[1], reqs[i]["schema"]), a[2])
                    except RuntimeError as exc:
                        a = ("error", RuntimeError("paid_" + str(exc)))
                out[i] = a
        return out
