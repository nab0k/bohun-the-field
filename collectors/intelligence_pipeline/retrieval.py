"""Bounded internal-first retrieval. Ranking is not verification or company identity."""
from datetime import datetime
import hashlib

from .contracts import aware, canonical_url, exact_fields, json_text, nonempty


def context_record(raw, as_of):
    exact_fields(raw, {"id", "kind", "status", "source_url", "available_at", "text", "text_hash",
                       "quote", "root_id"})
    for key in ("id", "kind", "status", "text", "quote", "root_id"):
        nonempty(raw[key])
    canonical_url(raw["source_url"])
    at = aware(datetime.fromisoformat(raw["available_at"].replace("Z", "+00:00")))
    if at > aware(as_of):
        raise ValueError("CONTEXT_LOOKAHEAD")
    if raw["kind"] not in {"source", "company_lead", "claim", "prior_assessment"}:
        raise ValueError("CONTEXT_KIND_INVALID")
    if raw["status"] not in {"reported", "lead", "verified", "hypothesis"}:
        raise ValueError("CONTEXT_STATUS_INVALID")
    if raw["kind"] == "company_lead" and raw["status"] != "lead":
        raise ValueError("LEAD_CANNOT_BE_VERIFIED")
    if len(raw["text"].encode()) > 32768 or raw["quote"] not in raw["text"]:
        raise ValueError("CONTEXT_QUOTE_OR_SIZE_INVALID")
    if hashlib.sha256(raw["text"].encode()).hexdigest() != raw["text_hash"]:
        raise ValueError("CONTEXT_HASH_INVALID")
    return dict(raw)


class InternalRetriever:
    def __init__(self, backend, *, max_records=4, max_bytes=65536):
        if type(max_records) is not int or not 1 <= max_records <= 4 or not 1 <= max_bytes <= 131072:
            raise ValueError("RETRIEVAL_LIMIT_INVALID")
        self.backend, self.max_records, self.max_bytes = backend, max_records, max_bytes

    def retrieve(self, document, *, as_of):
        raw = self.backend.context(document, as_of=aware(as_of), limit=self.max_records)
        if not isinstance(raw, list) or len(raw) > self.max_records:
            raise ValueError("CONTEXT_REPLY_EXCEEDS_LIMIT")
        records = [context_record(r, as_of) for r in raw]
        if len({r["id"] for r in records}) != len(records) or len(json_text(records).encode()) > self.max_bytes:
            raise ValueError("CONTEXT_DUPLICATE_OR_BYTE_LIMIT")
        return {"context_manifest": records, "context_status": "RETRIEVED" if records else "NO_MATCH_IN_SCOPE",
                "coverage": {"searched": "configured_internal_views", "as_of": aware(as_of).isoformat(),
                             "market_absence_proven": False,
                             "distinct_reported_roots": len({r["root_id"] for r in records if r["kind"] != "company_lead"}),
                             "independence_verified": False}}
