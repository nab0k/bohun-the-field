"""Gemini calls for the pilot: plain JSON calls, calls grounded with Google Search, and Batch mode.

Cost is charged from usageMetadata, including the tokens of search results (toolUsePromptTokenCount);
an unknown cost or a transport error stops the whole run without retry, as in synthesis.live. Batch
requests cost half (Batch mode, D-20261029-EXP028-COST); a batch answer without usage is charged at
its worst case. Spend is kept per step (grouping, research, structure, writer).
"""
from dataclasses import dataclass, field
import json
import re
import time
import urllib.error
import urllib.request

from ..synthesis.contracts import canonical
from ..synthesis.live import BudgetStop, GEMINI_URL, MODEL, http_post, micro_usd, response_schema

CAP_MICRO_USD = 2_000_000          # per run
MAX_SEARCH_QUERIES = 400           # 5 000 free per month; beyond that $14 per 1 000
SEARCH_RESERVE_TOKENS = 40_000     # search results enter the prompt and are not in the request body
BATCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:batchGenerateContent"
API_ROOT = "https://generativelanguage.googleapis.com/v1beta/"
BATCH_WAIT_SECONDS = 2700          # the workflow job has 60 minutes
BATCH_POLL_SECONDS = 20
BATCH_DONE = ("SUCCEEDED", "FAILED", "CANCELLED", "EXPIRED")


@dataclass
class Ledger:
    cap: int = CAP_MICRO_USD
    spent: int = 0
    calls: int = 0
    search_queries: int = 0
    steps: dict = field(default_factory=dict)

    def worst(self, body_bytes, max_output, grounded):
        return micro_usd(body_bytes + (SEARCH_RESERVE_TOKENS if grounded else 0), 2 * max_output)

    def reserve(self, worst, grounded):
        if self.spent + worst > self.cap:
            raise BudgetStop("BUDGET_CAP_WOULD_BE_EXCEEDED")
        if grounded and self.search_queries >= MAX_SEARCH_QUERIES:
            raise BudgetStop("SEARCH_QUERY_CAP_REACHED")

    def cost_of(self, usage):
        if not isinstance(usage, dict):
            raise BudgetStop("UNKNOWN_COST_NO_USAGE")
        counts = [usage.get("promptTokenCount"), usage.get("candidatesTokenCount", 0),
                  usage.get("thoughtsTokenCount", 0), usage.get("toolUsePromptTokenCount", 0)]
        if any(type(n) is not int or n < 0 for n in counts):
            raise BudgetStop("UNKNOWN_COST_INVALID_USAGE")
        inp, out, thoughts, tool = counts
        return micro_usd(inp + tool, out + thoughts)

    def add(self, cost, step):
        self.spent += cost
        self.calls += 1
        self.steps[step] = self.steps.get(step, 0) + cost

    def charge(self, usage, step="other", factor=1.0):
        cost = int(self.cost_of(usage) * factor)
        self.add(cost, step)
        if self.spent > self.cap:
            raise BudgetStop("BUDGET_CAP_REACHED")
        return cost


def parse_json_text(text):
    """JSON from a model answer; tolerates a Markdown code fence and trailing prose."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
    if start > 0:
        text = text[start:]
    return json.JSONDecoder().raw_decode(text)[0]


def http_get(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=60) as reply:
        return json.load(reply)


def find_inlined(obj):
    """The list of inlined batch answers; the documented path varies (response / dest / nested)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "inlinedResponses":
                return v if isinstance(v, list) else find_inlined(v)
            found = find_inlined(v)
            if found is not None:
                return found
    return None


def batch_state(op):
    return str((op.get("metadata") or {}).get("state") or op.get("state") or "")


def parse_reply(reply, plain):
    """(answer, info) from one GenerateContentResponse; info has search data, not cost."""
    candidates = reply.get("candidates") or []
    if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
        raise ValueError("MODEL_INCOMPLETE_OUTPUT")
    cand = candidates[0]
    grounding = cand.get("groundingMetadata") or {}
    parts = cand.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    info = {"search_queries": len(grounding.get("webSearchQueries") or []),
            "supports": [{"text": (g.get("segment") or {}).get("text", ""), "chunks": g.get("groundingChunkIndices") or []}
                         for g in grounding.get("groundingSupports") or []],
            "grounding_chunks": [{"uri": (c.get("web") or {}).get("uri"), "title": (c.get("web") or {}).get("title")}
                                 for c in grounding.get("groundingChunks") or []]}
    return (text if plain else unwrap(parse_json_text(text))), info


def unwrap(answer):
    """Batch answers sometimes wrap the object in a list; take the single object."""
    if isinstance(answer, list):
        objs = [a for a in answer if isinstance(a, dict)]
        if len(objs) == 1:
            return objs[0]
        if objs:
            merged = {}
            for o in objs:
                for k, v in o.items():
                    if isinstance(v, list):
                        merged.setdefault(k, []).extend(v)
                    else:
                        merged.setdefault(k, v)
            return merged
    return answer


class Gemini:
    def __init__(self, key, ledger, transport=http_post, thinking="low", getter=http_get, sleep=time.sleep,
                 batch=False):
        if not key:
            raise BudgetStop("GEMINI_API_KEY_ABSENT")
        self.key, self.ledger, self.transport, self.thinking = key, ledger, transport, thinking
        self.getter, self.sleep, self.batch = getter, sleep, batch
        self.batches = []

    def body(self, system, packet, schema, grounded, max_output, schema_mode, thinking=None, task=None):
        config = {"temperature": 0.2, "maxOutputTokens": max_output, "candidateCount": 1,
                  "thinkingConfig": {"thinkingLevel": thinking or self.thinking}}
        instruction = system
        if schema_mode == "schema":
            config["responseMimeType"] = "application/json"
            config["responseJsonSchema"] = response_schema(schema)
        elif schema_mode == "plain":
            pass  # free text: a JSON demand makes the model skip Google Search (probe run 40904136518)
        else:
            instruction += "\n\nReturn only one JSON object matching this schema, no prose:\n" + canonical(schema)
        body = {"systemInstruction": {"parts": [{"text": instruction}]},
                "contents": [{"role": "user", "parts": ([{"text": task}] if task else []) + [{"text": canonical(packet)}]}],
                "generationConfig": config}
        if grounded:
            body["tools"] = [{"google_search": {}}]
        return body

    def request(self, system, packet, schema, grounded=False, max_output=6000, schema_mode="schema", thinking=None,
                task=None, step="other"):
        return {"schema": schema, "schema_mode": schema_mode,"body": self.body(system, packet, schema, grounded, max_output, schema_mode, thinking, task),
                "grounded": grounded, "max_output": max_output, "plain": schema_mode == "plain", "step": step}

    def __call__(self, system, packet, schema, grounded=False, max_output=6000, schema_mode="schema",
                 thinking=None, task=None, retries=1, step="other"):
        req = self.request(system, packet, schema, grounded, max_output, schema_mode, thinking, task, step)
        for attempt in range(retries + 1):
            try:
                return self._direct(req)
            except json.JSONDecodeError:
                # Malformed JSON is billed and charged; one more attempt, then the event fails alone.
                if attempt == retries:
                    raise

    def many(self, reqs):
        """Answers in order: ("ok", answer, info) or ("error", exception). BudgetStop propagates."""
        if not reqs:
            return []
        if not self.batch or len(reqs) == 1:
            out = []
            for r in reqs:
                try:
                    out.append(("ok",) + self._retry(r))
                except BudgetStop:
                    raise
                except Exception as exc:
                    out.append(("error", exc))
            return out
        return self._batch(reqs)

    def as_text_mode(self, req):
        if req.get("schema_mode") != "schema":
            return req
        body = json.loads(json.dumps(req["body"]))
        config = body["generationConfig"]
        config.pop("responseMimeType", None); config.pop("responseJsonSchema", None)
        body["systemInstruction"]["parts"][0]["text"] += (
            "\n\nReturn only one JSON object matching this schema, no prose:\n" + canonical(req["schema"]))
        return dict(req, body=body, schema_mode="text")

    def _retry(self, req):
        try:
            return self._direct(req)
        except json.JSONDecodeError:
            return self._direct(req)

    def _direct(self, req):
        self.ledger.reserve(self.ledger.worst(len(canonical(req["body"]).encode()), req["max_output"], req["grounded"]),
                            req["grounded"])
        try:
            reply = self.transport(GEMINI_URL.format(MODEL), {"x-goog-api-key": self.key}, req["body"])
        except urllib.error.HTTPError as exc:
            raise BudgetStop(http_detail("MODEL", exc)) from None
        except Exception:
            raise BudgetStop("MODEL_CALL_UNCERTAIN_NO_RETRY") from None
        cost = self.ledger.charge(reply.get("usageMetadata"), req["step"])
        answer, info = parse_reply(reply, req["plain"])
        self.ledger.search_queries += info["search_queries"]
        info["cost_micro_usd"] = cost
        return answer, info

    def _batch(self, reqs):
        # Batch does not keep responseJsonSchema (run 40920575588): for batched requests only, the schema goes
        # into the prompt. Direct calls (grouping, single requests) keep structured output (run 40923499569).
        reqs = [self.as_text_mode(r) for r in reqs]
        worsts = [self.ledger.worst(len(canonical(r["body"]).encode()), r["max_output"], r["grounded"]) // 2
                  for r in reqs]
        self.ledger.reserve(sum(worsts), any(r["grounded"] for r in reqs))
        payload = {"batch": {"display_name": "kb-pilot-%s" % reqs[0]["step"], "input_config": {"requests": {
            "requests": [{"request": r["body"], "metadata": {"key": "r%d" % i}} for i, r in enumerate(reqs)]}}}}
        headers = {"x-goog-api-key": self.key}
        try:
            op = self.transport(BATCH_URL.format(MODEL), headers, payload)
        except urllib.error.HTTPError as exc:
            raise BudgetStop(http_detail("BATCH_CREATE", exc)) from None
        except Exception:
            raise BudgetStop("BATCH_CREATE_UNCERTAIN") from None
        name = op.get("name")
        if not name:
            raise BudgetStop("BATCH_NO_NAME")
        record = {"name": name, "step": reqs[0]["step"], "requests": len(reqs)}
        self.batches.append(record)
        waited = 0
        while not batch_state(op).endswith(BATCH_DONE):
            if waited >= BATCH_WAIT_SECONDS:
                raise BudgetStop("BATCH_NOT_DONE " + name)
            self.sleep(BATCH_POLL_SECONDS)
            waited += BATCH_POLL_SECONDS
            try:
                op = self.getter(API_ROOT + name, headers)
            except Exception:
                continue  # a failed poll is retried; the batch itself is not resent
        record.update(state=batch_state(op), waited_seconds=waited)
        if not batch_state(op).endswith("SUCCEEDED"):
            raise BudgetStop("BATCH_" + batch_state(op))
        answers = find_inlined(op) or []
        by_key = {}
        for k, a in enumerate(answers):
            key = ((a.get("metadata") or {}).get("key")) or "r%d" % k
            by_key[key] = a
        out = []
        for i, r in enumerate(reqs):
            a = by_key.get("r%d" % i)
            if not a or "response" not in a:
                self.ledger.add(worsts[i], r["step"])  # unknown outcome: charged at worst case
                out.append(("error", RuntimeError("BATCH_ITEM_" + ("MISSING" if not a else "ERROR"))))
                continue
            usage = a["response"].get("usageMetadata")
            try:
                cost = self.ledger.charge(usage, r["step"], factor=0.5)
            except BudgetStop as stop:
                if str(stop).startswith("UNKNOWN_COST"):
                    cost = worsts[i]; self.ledger.add(cost, r["step"]); record["estimated"] = record.get("estimated", 0) + 1
                else:
                    raise
            try:
                answer, info = parse_reply(a["response"], r["plain"])
            except json.JSONDecodeError:
                try:
                    out.append(("ok",) + self._direct(r))  # one direct retry at full price
                except BudgetStop:
                    raise
                except Exception as exc:
                    out.append(("error", exc))
                continue
            except Exception as exc:
                out.append(("error", exc)); continue
            self.ledger.search_queries += info["search_queries"]
            info["cost_micro_usd"] = cost
            out.append(("ok", answer, info))
        if self.ledger.spent > self.ledger.cap:
            raise BudgetStop("BUDGET_CAP_REACHED")
        return out


def http_detail(prefix, exc):
    try:
        detail = json.loads(exc.read().decode("utf-8", "replace"))["error"]["message"][:200]
    except Exception:
        detail = ""
    return ("%s_HTTP_%d_NO_RETRY %s" % (prefix, exc.code, detail)).strip()
