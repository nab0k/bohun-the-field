"""Read-only C3-10 shadow review of a locally exported synthesis snapshot.

No network, database, model, publisher or source fetch. Not a production gate.
The caller must not treat an historical PUBLISH as independent verification.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

from .core import (
    EvidenceBundle, EvidenceSource, PublishCandidate, review,
)

SECTIONS = (
    "headline", "what_happened", "context", "policy_direction",
    "bigger_picture", "supply_chain", "why_it_matters", "context_sources",
)
MAX_ROWS = 30


def _source_id(url: str) -> str:
    return "url-" + sha256(url.encode("utf-8")).hexdigest()[:20]


def _prepare(record: dict):
    if not isinstance(record, dict):
        raise ValueError("snapshot_record_not_object")
    sid, eid, version = record.get("id"), record.get("event_id"), record.get("version")
    if (type(sid) is not int or sid < 1 or type(eid) is not int or eid < 1
            or type(version) is not int or version < 1):
        raise ValueError("invalid_snapshot_identity")
    source_rows = record.get("sources")
    if not isinstance(source_rows, list):
        raise ValueError("invalid_snapshot_sources")
    payload = {k: record[k] for k in SECTIONS if k in record}
    manifest, evidence_sources = [], []
    for src in source_rows:
        if not isinstance(src, dict):
            raise ValueError("invalid_snapshot_source_row")
        url = src.get("url")
        if not isinstance(url, str):
            raise ValueError("invalid_snapshot_source_url")
        source_id = _source_id(url)
        manifest.append({**src, "source_id": source_id})
        try:
            host = urlsplit(url).hostname or ""
        except ValueError:
            host = ""  # Preserve the malformed URL; core.review must BLOCK it.
        evidence_sources.append(EvidenceSource(
            source_id=source_id,
            url=url,
            root_source_id=host,
            published_at=src.get("date") or "",
            spans=(),  # Captured source text is NOT trusted evidence here.
            retrieval_verified=False,  # Never accept source flags from a JSON export.
        ))
    payload["sources"] = manifest
    published_at = record.get("published_at")
    if isinstance(published_at, str) and len(published_at) >= 10:
        payload["as_of"] = published_at[:10]
    candidate = PublishCandidate(
        material_id="syn-" + str(sid),
        material_type="synthesis",
        version=version,
        payload=payload,
        author_model_id=record.get("generator") or "",
        event_ids=("event-" + str(eid),),
        event_fingerprint="",  # Historical export is not an authoritative fingerprint.
    )
    evidence = EvidenceBundle(
        sources=tuple(evidence_sources),
        event_ids=("event-" + str(eid),),
    )
    return candidate, evidence


def shadow_review(snapshot: list) -> dict:
    """Run the exact offline gate without creating trust from a stored PUBLISH.

    Returns only IDs, result codes and aggregate counters, never article text.
    """
    if not isinstance(snapshot, list) or not 1 <= len(snapshot) <= MAX_ROWS:
        raise ValueError("snapshot must contain 1..30 synthesis rows")
    seen = set()
    results = []
    for rec in snapshot:
        candidate, evidence = _prepare(rec)
        if candidate.material_id in seen:
            raise ValueError("duplicate_snapshot_material")
        seen.add(candidate.material_id)
        v = review(candidate, evidence, assessment=None, published=())
        if v.decision == "PASS":
            raise AssertionError("shadow replay must not create a publishable PASS")
        flags = ["trusted_checker_missing", "authoritative_duplicate_index_missing"]
        if evidence.sources:
            flags.append("retrieval_provenance_missing")
        if candidate.payload.get("context") or candidate.payload.get("context_sources"):
            flags.append("context_evidence_linkage_unverified")
        results.append({
            "material_id": candidate.material_id,
            "version": candidate.version,
            "decision": v.decision,
            "reason_codes": sorted({r.code for r in v.reasons}),
            "integration_gaps": sorted(flags),
            "sources_count": len(evidence.sources),
            "offline_only": True,
            "publication_authorized": False,
        })
    decisions = Counter(r["decision"] for r in results)
    return {
        "schema": "c3-10-shadow-report-v1",
        "mode": "read-only-offline-no-model",
        "count": len(results),
        "decisions": {key: decisions[key] for key in ("PASS", "REVIEW", "BLOCK")},
        "publication_authorized": False,
        "results": results,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True,
                        help="private local JSON export of public synthesis metadata and sections")
    parser.add_argument("--report", type=Path, required=True,
                        help="private local output, never the website or project public directory")
    args = parser.parse_args(argv)
    rows = json.loads(args.snapshot.read_text(encoding="utf-8"))
    result = shadow_review(rows)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({"count": result["count"], "decisions": result["decisions"],
                      "publication_authorized": False}, sort_keys=True))


if __name__ == "__main__":
    main()
