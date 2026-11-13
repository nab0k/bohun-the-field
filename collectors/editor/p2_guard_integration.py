"""P2-only diagnostic bridge: select the linked capture, not unrelated evidence.

This does NOT verify that capture contents are authentic; caller checks lineage.
Never changes checker request/hash, semantic verdict or publisher permissions.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from urllib.parse import urlparse

from .evidence_guard import check_claim_p2


def assess_p2_packet(packet: Mapping, *, expected_p2_url: str) -> dict:
    if not isinstance(packet, Mapping) or packet.get("schema") != "bohun-c3-10-checker-probe-v1":
        raise ValueError("invalid_p2_guard_packet")
    if not isinstance(expected_p2_url, str):
        raise ValueError("invalid_p2_guard_url")
    url = urlparse(expected_p2_url)
    if url.scheme != "https" or not url.netloc or url.username or url.password:
        raise ValueError("invalid_p2_guard_url")
    claims, spans = packet.get("claims"), packet.get("spans")
    if (not isinstance(claims, list) or not 1 <= len(claims) <= 5
            or not isinstance(spans, list) or not 1 <= len(spans) <= 128):
        raise ValueError("invalid_p2_guard_counts")
    seen_refs, linked_sources, linked, excluded = set(), set(), [], 0
    for span in spans:
        if (not isinstance(span, Mapping)
                or not {"source_id", "span_id", "url", "quote"}.issubset(span)
                or not isinstance(span["source_id"], str) or not span["source_id"]
                or not isinstance(span["span_id"], str) or not span["span_id"]
                or not isinstance(span["url"], str) or not span["url"]
                or not isinstance(span["quote"], str)
                or not 1 <= len(span["quote"]) <= 200):
            raise ValueError("p2_guard_source_lineage_mismatch")
        ref = (span["source_id"], span["span_id"])
        if ref in seen_refs:
            raise ValueError("p2_guard_source_lineage_mismatch")
        seen_refs.add(ref)
        if span["url"] == expected_p2_url:
            linked_sources.add(span["source_id"])
            linked.append({"span_id": span["span_id"], "text": span["quote"]})
        else:
            excluded += 1
    if not linked or len(linked_sources) != 1:
        raise ValueError("p2_guard_missing_or_multiple_linked_sources")
    if len(linked) > 10:
        raise ValueError("invalid_p2_guard_counts")
    seen_claims, findings, count = set(), [], Counter()
    for claim in claims:
        if (not isinstance(claim, Mapping)
                or not isinstance(claim.get("claim_id"), str)
                or not claim["claim_id"] or claim["claim_id"] in seen_claims
                or not isinstance(claim.get("text"), str)):
            raise ValueError("p2_guard_invalid_claim")
        seen_claims.add(claim["claim_id"])
        outcome = check_claim_p2(claim["text"], linked)
        codes = [f["code"] for f in outcome["findings"]]
        count.update(codes)
        if codes:
            findings.append({"claim_id": claim["claim_id"], "codes": codes})
    return {
        "schema": "bohun-c3-10-p2-guard-aggregate-v1",
        "claim_count": len(claims),
        "span_count": len(linked),
        "excluded_foreign_spans": excluded,
        "flagged_claims": len(findings),
        "flags_by_code": dict(sorted(count.items())),
        "flags_by_claim": findings,
        "evidence_basis": "caller_verified_linked_P2_only",
        "independent_p1_attested": False,
        "semantic_accuracy_attested": False,
        "publication_authorized": False,
        "diagnostic_only": True,
    }
