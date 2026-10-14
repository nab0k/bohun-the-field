"""Opt-in Gemini adjudication with a fail-closed preflight budget ledger."""

import json
from dataclasses import dataclass, field

import requests


@dataclass
class BudgetLedger:
    cap_usd: float
    input_per_million: float = 0.10
    output_per_million: float = 0.40
    spent_usd: float = 0.0
    entries: list = field(default_factory=list)

    def reserve(self, input_tokens, max_output_tokens):
        estimate = input_tokens / 1_000_000 * self.input_per_million + max_output_tokens / 1_000_000 * self.output_per_million
        if self.spent_usd + estimate > self.cap_usd + 1e-12:
            raise RuntimeError("GEMINI_BUDGET_BLOCKED")
        self.spent_usd += estimate
        self.entries.append({"input_tokens": input_tokens, "max_output_tokens": max_output_tokens, "reserved_usd": round(estimate, 8)})
        return estimate


def adjudicate(rows, *, api_key, cap_usd, model="gemini-3.5-flash-lite", session=None):
    if not 0 < cap_usd <= 1.0:
        raise ValueError("gemini_cap_usd must be > 0 and <= 1.00")
    session = session or requests.Session()
    ledger = BudgetLedger(cap_usd)
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}"
    output = []
    for offset in range(0, len(rows), 50):
        batch = rows[offset:offset + 50]
        payload_text = json.dumps([{"cluster_id": row["cluster_id"], "title": row["title"], "publisher": row.get("publisher", ""), "date": row.get("first_seen_at", "")} for row in batch], ensure_ascii=False)
        count = session.post(endpoint + ":countTokens", params={"key": api_key}, json={"contents": [{"parts": [{"text": payload_text}]}]}, timeout=15)
        count.raise_for_status()
        input_tokens = int(count.json().get("totalTokens", 0))
        ledger.reserve(input_tokens, 2048)
        prompt = "Classify metadata only. Return JSON array with cluster_id,relevant(yes|no|unsure),signal,region,reason.\n" + payload_text
        response = session.post(endpoint + ":generateContent", params={"key": api_key}, json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": 2048}}, timeout=30)
        response.raise_for_status()
        text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
        output.extend(json.loads(text))
    return output, ledger

