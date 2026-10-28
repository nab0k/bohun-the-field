"""Bounded context from a supplied snapshot; never from past synthesis prose."""
from datetime import timedelta
from .editorial import date


def context_packet(snapshot, topic_slug, as_of):
    cutoff = date(as_of)
    facts = []
    # Callers must supply source-bound records, not writer output. An empty
    # layer stays empty. Live SQL acquisition is intentionally not wired here.
    limits = {"context": 8, "policy_direction": 2, "bigger_picture": 1, "supply_chain": 8}
    for block, limit in limits.items():
        rows = []
        for row in snapshot.get(block, []):
            if row.get("topic") != topic_slug or row.get("origin") != "source_fact":
                continue
            if not row.get("quote") or row["quote"] not in row.get("source_text", ""):
                continue
            if not row.get("date") or date(row["date"]) > cutoff:
                continue
            # The 18-month window bounds the topic chronology; a doctrine stays valid until replaced.
            if block == "context" and date(row["date"]) < cutoff-timedelta(days=548):
                continue
            if block == "supply_chain" and (row.get("depth", 1) > 2 or row.get("status", "in_force") not in {"in_force", "suspended"}):
                continue
            rows.append(row)
        for row in sorted(rows, key=lambda x: (x["date"], x["id"]), reverse=True)[:limit]:
            facts.append({k: v for k, v in {**row, "block": block}.items() if k != "source_text"})
    return facts
