"""Programmatic spending guard for paid Gemini calls (Phase 1 experiments).

THIS IS A SOFTWARE LIMIT, NOT A BILLING GUARANTEE. The Gemini API offers no
per-request or per-key hard money cap that this code can set, so every call goes
through `BudgetLedger.generate()`, which:

1. counts input tokens first with the free models.countTokens endpoint;
2. computes the worst-case cost of the call: input_tokens * input_price +
   max_output_tokens * output_price (Google: max_output_tokens INCLUDES thinking
   tokens, and output price includes thinking tokens);
3. refuses the call if spent_so_far + worst_case > cap, or if call/size limits
   would be exceeded;
4. makes exactly one attempt (no retries) and books the ACTUAL cost from
   usageMetadata (prompt + candidates + thoughts tokens), or the worst case if
   the response carries no usage data.

Prices: ai.google.dev/gemini-api/docs/pricing, checked 2026-10-13, paid tier,
gemini-3.5-flash-lite: $0.30 / 1M input, $2.50 / 1M output (incl. thinking).
If the key is on the free tier the billed amount is $0; the ledger still books
paid-tier prices as an upper bound.
"""
import json
import time
import urllib.error
import urllib.request

PRICES = {
    "gemini-3.5-flash-lite": {"input_per_m": 0.30, "output_per_m": 2.50,
                              "source": "https://ai.google.dev/gemini-api/docs/pricing", "checked": "2026-10-13"},
}
API = "https://generativelanguage.googleapis.com/v1beta/models/"


class BudgetExceeded(RuntimeError):
    pass


class BudgetLedger:
    def __init__(self, *, api_key, model, cap_usd, max_calls, max_input_tokens_per_call,
                 max_output_tokens, opener=None, clock=time.perf_counter):
        if model not in PRICES:
            raise BudgetExceeded("NO_VERIFIED_PRICE_FOR_MODEL:" + model)
        self.api_key = api_key
        self.model = model
        self.price = PRICES[model]
        self.cap_usd = cap_usd
        self.max_calls = max_calls
        self.max_input_tokens_per_call = max_input_tokens_per_call
        self.max_output_tokens = max_output_tokens
        self._open = opener or self._http
        self._clock = clock
        self.spent_usd = 0.0
        self.calls = []
        self.stopped = None

    # --- transport -------------------------------------------------------------
    def _http(self, url, payload):
        req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.load(resp)

    def cost(self, input_tokens, output_tokens):
        return input_tokens * self.price["input_per_m"] / 1e6 + output_tokens * self.price["output_per_m"] / 1e6

    def worst_case(self, input_tokens):
        return self.cost(input_tokens, self.max_output_tokens)

    # --- guarded call ------------------------------------------------------------
    def generate(self, contents, generation_config, tag):
        entry = {"tag": tag, "model": self.model, "attempt": 1}
        if self.stopped:
            raise BudgetExceeded("LEDGER_STOPPED:" + self.stopped)
        if len(self.calls) >= self.max_calls:
            self.stopped = "MAX_CALLS"
            raise BudgetExceeded("MAX_CALLS_REACHED")
        gen = dict(generation_config)
        gen["maxOutputTokens"] = min(int(gen.get("maxOutputTokens", self.max_output_tokens)), self.max_output_tokens)
        try:
            counted = self._open(API + self.model + ":countTokens", {"contents": contents})["totalTokens"]
        except Exception as exc:  # cannot bound the cost -> no paid call
            entry.update(outcome="COUNT_TOKENS_FAILED:" + type(exc).__name__, cost_usd=0.0)
            self.calls.append(entry)
            raise BudgetExceeded("COUNT_TOKENS_FAILED") from exc
        worst = self.cost(counted, gen["maxOutputTokens"])
        entry.update(counted_input_tokens=counted, max_output_tokens=gen["maxOutputTokens"],
                     worst_case_usd=round(worst, 6), spent_before_usd=round(self.spent_usd, 6))
        if counted > self.max_input_tokens_per_call:
            entry.update(outcome="REFUSED_INPUT_TOO_LARGE", cost_usd=0.0)
            self.calls.append(entry)
            raise BudgetExceeded("INPUT_TOKENS_OVER_LIMIT")
        if self.spent_usd + worst > self.cap_usd:
            self.stopped = "CAP"
            entry.update(outcome="REFUSED_BUDGET_CAP", cost_usd=0.0)
            self.calls.append(entry)
            raise BudgetExceeded("BUDGET_CAP_WOULD_BE_EXCEEDED")
        t0 = self._clock()
        try:
            result = self._open(API + self.model + ":generateContent",
                                {"contents": contents, "generationConfig": gen})
        except urllib.error.HTTPError as exc:
            # Unknown whether the provider billed a failed call: book the worst case.
            self.spent_usd += worst
            entry.update(outcome=f"HTTP_{exc.code}", cost_usd=round(worst, 6), cost_basis="worst_case_on_error",
                         latency_s=round(self._clock() - t0, 3))
            self.calls.append(entry)
            raise
        except Exception as exc:
            self.spent_usd += worst
            entry.update(outcome="ERROR:" + type(exc).__name__, cost_usd=round(worst, 6), cost_basis="worst_case_on_error",
                         latency_s=round(self._clock() - t0, 3))
            self.calls.append(entry)
            raise
        usage = result.get("usageMetadata") or {}
        prompt = usage.get("promptTokenCount")
        out = (usage.get("candidatesTokenCount") or 0)
        thoughts = (usage.get("thoughtsTokenCount") or 0)
        if prompt is None:
            cost, basis = worst, "worst_case_no_usage"
        else:
            cost, basis = self.cost(prompt, out + thoughts), "usageMetadata"
        self.spent_usd += cost
        finish = (result.get("candidates") or [{}])[0].get("finishReason")
        entry.update(outcome="OK", latency_s=round(self._clock() - t0, 3), model_version=result.get("modelVersion"),
                     prompt_tokens=prompt, output_tokens=out, thought_tokens=thoughts, finish_reason=finish,
                     cost_usd=round(cost, 6), cost_basis=basis, spent_after_usd=round(self.spent_usd, 6))
        self.calls.append(entry)
        return result

    def report(self):
        ok = [c for c in self.calls if c.get("outcome") == "OK"]
        return {
            "limit_type": "PROGRAMMATIC_ESTIMATE_NOT_BILLING_GUARANTEE",
            "model": self.model, "price": self.price, "cap_usd": self.cap_usd,
            "max_calls": self.max_calls, "max_input_tokens_per_call": self.max_input_tokens_per_call,
            "max_output_tokens_per_call_incl_thinking": self.max_output_tokens, "retries": 0,
            "calls_attempted": len(self.calls), "calls_ok": len(ok),
            "prompt_tokens": sum(c.get("prompt_tokens") or 0 for c in ok),
            "output_tokens": sum(c.get("output_tokens") or 0 for c in ok),
            "thought_tokens": sum(c.get("thought_tokens") or 0 for c in ok),
            "spent_usd_paid_tier_estimate": round(self.spent_usd, 6),
            "model_versions": sorted({c.get("model_version") for c in ok if c.get("model_version")}),
            "stopped": self.stopped,
        }


def budgeted_transport(ledger, tag):
    """Adapter for GeminiDirectExtractor(transport=...)."""
    def transport(body):
        req = json.loads(body)
        return ledger.generate(req["contents"], req["generationConfig"], tag)
    return transport


def budgeted_langextract_model(ledger, tag, max_output_tokens):
    """A LangExtract BaseLanguageModel that sends every chunk through the ledger."""
    from langextract.core import base_model, types

    class BudgetedGemini(base_model.BaseLanguageModel):
        def infer(self, batch_prompts, **kwargs):
            for prompt in batch_prompts:
                res = ledger.generate([{"parts": [{"text": prompt}]}],
                                      {"responseMimeType": "application/json", "temperature": 0,
                                       "maxOutputTokens": max_output_tokens}, tag)
                parts = (res.get("candidates") or [{}])[0].get("content", {}).get("parts") or [{}]
                yield [types.ScoredOutput(score=1.0, output=parts[0].get("text", ""))]

    return BudgetedGemini()
