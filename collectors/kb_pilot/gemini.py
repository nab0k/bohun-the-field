"""Gemini calls for the pilot: plain JSON calls and calls grounded with Google Search.

Cost is charged from usageMetadata, including the tokens of search results (toolUsePromptTokenCount);
an unknown cost or a transport error stops the whole run without retry, as in synthesis.live.
"""
from dataclasses import dataclass
import json
import re
import urllib.error

from ..synthesis.contracts import canonical
from ..synthesis.live import BudgetStop, GEMINI_URL, MODEL, http_post, micro_usd, response_schema

CAP_MICRO_USD = 2_000_000          # D-20261029-EXP028-G3-PERMISSIONS: $2 for the pilot
MAX_SEARCH_QUERIES = 400           # 5 000 free per month; beyond that $14 per 1 000
SEARCH_RESERVE_TOKENS = 40_000     # search results enter the prompt and are not in the request body
SEARCH_PRICE_MICRO_USD = 14_000    # per query, counted only to report a worst case


@dataclass
class Ledger:
    cap: int = CAP_MICRO_USD
    spent: int = 0
    calls: int = 0
    search_queries: int = 0

    def reserve(self, body_bytes, max_output, grounded):
        extra = SEARCH_RESERVE_TOKENS if grounded else 0
        worst = micro_usd(body_bytes + extra, 2 * max_output)
        if self.spent + worst > self.cap:
            raise BudgetStop("BUDGET_CAP_WOULD_BE_EXCEEDED")
        if grounded and self.search_queries >= MAX_SEARCH_QUERIES:
            raise BudgetStop("SEARCH_QUERY_CAP_REACHED")

    def charge(self, usage):
        if not isinstance(usage, dict):
            raise BudgetStop("UNKNOWN_COST_NO_USAGE")
        counts = [usage.get("promptTokenCount"), usage.get("candidatesTokenCount", 0),
                  usage.get("thoughtsTokenCount", 0), usage.get("toolUsePromptTokenCount", 0)]
        if any(type(n) is not int or n < 0 for n in counts):
            raise BudgetStop("UNKNOWN_COST_INVALID_USAGE")
        inp, out, thoughts, tool = counts
        cost = micro_usd(inp + tool, out + thoughts)
        self.spent += cost
        self.calls += 1
        if self.spent > self.cap:
            raise BudgetStop("BUDGET_CAP_REACHED")
        return cost


def parse_json_text(text):
    """JSON from a model answer; tolerates a Markdown code fence around it."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.loads(text)


class Gemini:
    def __init__(self, key, ledger, transport=http_post, thinking="low"):
        if not key:
            raise BudgetStop("GEMINI_API_KEY_ABSENT")
        self.key, self.ledger, self.transport, self.thinking = key, ledger, transport, thinking

    def body(self, system, packet, schema, grounded, max_output, schema_mode):
        config = {"temperature": 0.2, "maxOutputTokens": max_output, "candidateCount": 1,
                  "thinkingConfig": {"thinkingLevel": self.thinking}}
        instruction = system
        if schema_mode == "schema":
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = response_schema(schema)
        else:
            instruction += "\n\nReturn only one JSON object matching this schema, no prose:\n" + canonical(schema)
        body = {"serviceTier": "standard", "systemInstruction": {"parts": [{"text": instruction}]},
                "contents": [{"role": "user", "parts": [{"text": canonical(packet)}]}],
                "generationConfig": config}
        if grounded:
            body["tools"] = [{"google_search": {}}]
        return body

    def __call__(self, system, packet, schema, grounded=False, max_output=6000, schema_mode="schema"):
        body = self.body(system, packet, schema, grounded, max_output, schema_mode)
        self.ledger.reserve(len(canonical(body).encode()), max_output, grounded)
        try:
            reply = self.transport(GEMINI_URL.format(MODEL), {"x-goog-api-key": self.key}, body)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace"))["error"]["message"][:200]
            except Exception:
                detail = ""
            raise BudgetStop(("MODEL_HTTP_%d_NO_RETRY " % exc.code + detail).strip()) from None
        except Exception:
            raise BudgetStop("MODEL_CALL_UNCERTAIN_NO_RETRY") from None
        cost = self.ledger.charge(reply.get("usageMetadata"))
        candidates = reply.get("candidates") or []
        if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
            raise ValueError("MODEL_INCOMPLETE_OUTPUT")
        cand = candidates[0]
        grounding = cand.get("groundingMetadata") or {}
        queries = grounding.get("webSearchQueries") or []
        self.ledger.search_queries += len(queries)
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        chunks = [{"uri": (c.get("web") or {}).get("uri"), "title": (c.get("web") or {}).get("title")}
                  for c in grounding.get("groundingChunks") or []]
        return parse_json_text(text), {"cost_micro_usd": cost, "search_queries": len(queries),
                                       "grounding_chunks": chunks}
