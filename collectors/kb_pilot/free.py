"""Free models for the steps without search (D-20261029-EXP028-CHEAP-SCHEME): Cloudflare Workers AI through its
OpenAI-compatible endpoint, with Gemini as the fallback for every request that fails, returns bad JSON or would
pass the free daily allocation. Same interface as gemini.Gemini (request / __call__ / many).

The key comes from the environment only (CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_WORKERS_AI_TOKEN); without it every
request goes to Gemini, so the pilot runs the same with or without the free model.
"""
from concurrent.futures import ThreadPoolExecutor
import json
import os
import urllib.error
import urllib.request

from ..synthesis.contracts import canonical
from ..synthesis.live import BudgetStop
from .gemini import parse_json_text, unwrap

WORKERS_AI_URL = "https://api.cloudflare.com/client/v4/accounts/{}/ai/v1/chat/completions"
FREE_MODEL = os.environ.get("KB_FREE_MODEL", "@cf/google/gemma-4-26b-a4b-it")
# Neurons per million tokens for gemma-4-26b-a4b-it ($0.10 / $0.30 per M at $0.011 per 1 000 neurons).
NEURONS_PER_M_IN, NEURONS_PER_M_OUT = 9_091, 27_273
FREE_NEURONS_PER_RUN = 9_000      # stay under the 10 000 free neurons per day
WORKERS = 3
REQUIRED_NONEMPTY = {"what_happened", "groups"}
MIN_OUTPUT_TOKENS = 8000  # free models reason before answering; a small cap cuts the JSON (run 40934038923)


def http_post_json(url, headers, body, timeout=300):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as reply:
        return json.load(reply)


class FreeModel:
    def __init__(self, fallback, account=None, token=None, transport=http_post_json, model=FREE_MODEL):
        self.fallback, self.transport, self.model = fallback, transport, model
        self.account = (account if account is not None else os.environ.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
        self.token = (token if token is not None else os.environ.get("CLOUDFLARE_WORKERS_AI_TOKEN") or "").strip()
        self.ledger = fallback.ledger
        self.batches = fallback.batches
        self.neurons = 0
        self.stats = {"free_ok": 0, "fallback": {}}

    @property
    def enabled(self):
        return bool(self.account and self.token)

    def request(self, system, packet, schema, grounded=False, max_output=6000, schema_mode="schema", thinking=None,
                task=None, step="other"):
        if grounded or schema_mode == "plain":
            raise ValueError("FREE_MODEL_HAS_NO_SEARCH")  # search and free text stay on Gemini
        gem = self.fallback.request(system, packet, schema, False, max_output, schema_mode, thinking, task, step)
        user = ((task + "\n\n") if task else "") + canonical(packet)
        body = {"model": self.model, "temperature": 0.2, "max_tokens": max(max_output, MIN_OUTPUT_TOKENS),
                "response_format": {"type": "json_object"},
                # Reasoning off (Workers AI docs, gemma-4 / nemotron): with it on, every large step ran out of
                # tokens before the JSON (run 40935846507: 12/13 truncated at 8000 tokens).
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "system", "content": system + "\n\nReturn only one JSON object matching this "
                                                                    "schema, no prose:\n" + canonical(schema)},
                             {"role": "user", "content": user}]}
        return {"free_body": body, "gemini": gem, "step": step, "chars": len(user) + len(system), "schema": schema}

    def __call__(self, *args, **kwargs):
        out = self.many([self.request(*args, **kwargs)])[0]
        if out[0] == "error":
            raise out[1]
        return out[1], out[2]

    def _free(self, req):
        if not self.enabled:
            raise RuntimeError("free_disabled")
        est = (req["chars"] // 3) * NEURONS_PER_M_IN // 1_000_000 + 1
        if self.neurons + est > FREE_NEURONS_PER_RUN:
            raise RuntimeError("free_allocation")
        try:
            reply = self.transport(WORKERS_AI_URL.format(self.account), {"Authorization": "Bearer " + self.token},
                                   req["free_body"])
        except urllib.error.HTTPError as exc:
            raise RuntimeError("free_http_%d" % exc.code) from None
        except Exception as exc:
            raise RuntimeError("free_transport_" + type(exc).__name__) from None
        usage = reply.get("usage") or {}
        self.neurons += (int(usage.get("prompt_tokens") or 0) * NEURONS_PER_M_IN
                         + int(usage.get("completion_tokens") or 0) * NEURONS_PER_M_OUT) // 1_000_000 + 1
        choice = (reply.get("choices") or [{}])[0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("free_truncated")
        try:
            text = (choice.get("message") or {}).get("content") or ""
            answer = unwrap(parse_json_text(text))
        except Exception:
            raise RuntimeError("free_bad_json_len%d" % min(len(text) // 1000, 9)) from None
        if not isinstance(answer, dict):
            raise RuntimeError("free_not_object")
        missing = [k for k in (req.get("schema") or {}).get("required", [])
                   if k not in answer or (isinstance(answer[k], str) and not answer[k].strip())
                   or (k in REQUIRED_NONEMPTY and not answer[k])]
        if missing:  # run 40938142142: the free writer returned an empty headline and sections
            raise RuntimeError("free_incomplete")
        self.ledger.steps["free_" + req["step"]] = self.ledger.steps.get("free_" + req["step"], 0)
        return answer, {"cost_micro_usd": 0, "search_queries": 0, "supports": [], "grounding_chunks": [],
                        "model": self.model, "free_tokens": usage}

    def many(self, reqs):
        """Free model first (in parallel); the requests it could not answer go to Gemini together (batch if on)."""
        if not reqs:
            return []
        out = [None] * len(reqs)

        def attempt(i):
            try:
                out[i] = ("ok",) + self._free(reqs[i])
                self.stats["free_ok"] += 1
            except RuntimeError as exc:
                reason = str(exc)
                self.stats["fallback"][reason] = self.stats["fallback"].get(reason, 0) + 1
        if self.enabled:
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                list(pool.map(attempt, range(len(reqs))))
        else:
            self.stats["fallback"]["free_disabled"] = self.stats["fallback"].get("free_disabled", 0) + len(reqs)
        rest = [i for i, o in enumerate(out) if o is None]
        if rest:
            answers = self.fallback.many([reqs[i]["gemini"] for i in rest])
            for i, a in zip(rest, answers):
                out[i] = a
        return out
