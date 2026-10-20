"""BOHUN EXP-009 R3: offline preflight for the *merged* PR #73 contract.

Review-only. No network, files, database writes, source registration, or publication.
This code intentionally does not assert that a source API returned complete data.

Pass outputs from normalize.prepare(). The function returns only anonymous counters,
non-sensitive SHA256 identity keys, and HOLD decisions. Do NOT call it an importer.
"""
from collections import defaultdict
import hashlib
import re

CONTRACT = "bohun-source-review-v1"
HASH = re.compile(r"[0-9a-f]{64}\Z")
SCOPES = {
    "canadabuys": {"ca_new", "ca_open"},
    "uk_fts": {"one_hour"},
    "eu_funding": {"edf_grants"},
}
KINDS = {
    "canadabuys": {"procurement_notice"},
    "uk_fts": {"procurement_notice", "contract_award_notice", "procurement_other_release"},
    "eu_funding": {"grant_call_candidate", "procurement_notice", "eu_unclassified"},
}
MAX_BATCH = 20_000
MAX_SCOPES = 12


class ReviewGateStop(ValueError):
    """Reject nonconforming material before any operational integration."""


def _hash_identifier(*parts):
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _valid_scope(source, scope):
    if not isinstance(scope, str) or not scope or len(scope) > 140 or "\n" in scope:
        return False
    if scope in SCOPES[source]:
        return True
    if source == "uk_fts":
        return bool(re.fullmatch(r"uk_window:[0-9TZ:./+-]{20,120}", scope))
    if source == "eu_funding":
        return bool(re.fullmatch(r"eu_edf:[a-z0-9._:-]{1,120}", scope))
    return False


def validate_manifest(manifest):
    """Require exactly the safety envelope emitted by main-branch normalize.prepare."""
    if not isinstance(manifest, dict) or manifest.get("contract_version") != CONTRACT:
        raise ReviewGateStop("contract_mismatch")
    source = manifest.get("source_system")
    if not isinstance(source, str) or source not in SCOPES:
        raise ReviewGateStop("source_unknown")
    scope = manifest.get("collection_scope")
    if not _valid_scope(source, scope):
        raise ReviewGateStop("scope_unknown")
    if (manifest.get("status") != "PREPARED_UNVERIFIED" or
        manifest.get("qa") != "HOLD" or manifest.get("public_release") is not False):
        raise ReviewGateStop("unsafe_manifest_state")
    rows = manifest.get("items")
    if not isinstance(rows, list) or len(rows) > MAX_BATCH:
        raise ReviewGateStop("invalid_manifest_rows")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ReviewGateStop("record_not_object")
        identifier, title = row.get("external_id"), row.get("title")
        if (not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 1024 or
            not isinstance(title, str) or not title.strip() or len(title) > 2000):
            raise ReviewGateStop("invalid_record_identity_or_title")
        if identifier in seen:
            raise ReviewGateStop("duplicate_record_identity")
        seen.add(identifier)
        kind = row.get("item_type_candidate")
        if row.get("source_system") != source or not isinstance(kind, str) or kind not in KINDS[source]:
            raise ReviewGateStop("record_source_or_kind_mismatch")
        meta = row.get("source_metadata")
        if not isinstance(meta, dict) or meta.get("source_system") != source or meta.get("source_format") != "PUBLIC_METADATA":
            raise ReviewGateStop("record_provenance_mismatch")
        if (row.get("capture_status") != "METADATA_ONLY" or
            row.get("verification_status") != "HOLD" or
            row.get("relevance_status") != "UNVERIFIED" or
            row.get("public_release") is not False):
            raise ReviewGateStop("unsafe_record_state")
        digest = row.get("source_record_sha256")
        if not isinstance(digest, str) or not HASH.fullmatch(digest):
            raise ReviewGateStop("invalid_record_digest")
        if source == "uk_fts":
            if "::" not in identifier:
                raise ReviewGateStop("uk_release_identity_required")
            ocid, release = identifier.split("::", 1)
            if not ocid or not release:
                raise ReviewGateStop("uk_release_identity_invalid")
    return source, scope, rows


def plan(manifests, *, receipts=None):
    """Return a metadata-only HOLD report. A report never authorizes database writes.

    Optional receipts are caller-reported diagnostic flags, not proof of completeness.
    No raw titles, identifiers, source payloads or user-provided URLs are returned.
    """
    if not isinstance(manifests, list) or not manifests or len(manifests) > MAX_SCOPES:
        raise ReviewGateStop("manifest_count_invalid")
    sightings = defaultdict(list)
    procedures = defaultdict(set)
    scopes = set()
    for manifest in manifests:
        source, scope, rows = validate_manifest(manifest)
        if (source, scope) in scopes:
            raise ReviewGateStop("repeated_source_scope")
        scopes.add((source, scope))
        for row in rows:
            identifier = row["external_id"]
            digest = row["source_record_sha256"]
            sightings[(source, identifier)].append((scope, digest))
            if source == "uk_fts":
                ocid, _release = identifier.split("::", 1)
                procedures[ocid].add(identifier)

    coverage = []
    if receipts is not None:
        if not isinstance(receipts, list) or len(receipts) != len(scopes):
            raise ReviewGateStop("receipt_set_invalid")
        found = set()
        for receipt in receipts:
            if not isinstance(receipt, dict):
                raise ReviewGateStop("receipt_not_object")
            rsource, rscope = receipt.get("source_system"), receipt.get("collection_scope")
            if not isinstance(rsource, str) or not isinstance(rscope, str):
                raise ReviewGateStop("receipt_scope_or_completeness_invalid")
            key = (rsource, rscope)
            if key not in scopes or key in found or not isinstance(receipt.get("complete"), bool):
                raise ReviewGateStop("receipt_scope_or_completeness_invalid")
            found.add(key)
            coverage.append({"source_system": key[0], "collection_scope": key[1],
                             "claimed_complete": receipt["complete"], "independently_verified": False})

    multi_scope = []
    for (source, external_id), obs in sightings.items():
        if len(obs) > 1:
            multi_scope.append({
                "identity_sha256": _hash_identifier(source, external_id),
                "source_system": source,
                "sighting_count": len(obs),
                "scope_names": sorted(scope for scope, _ in obs),
                "distinct_payload_hashes": len({hashval for _, hashval in obs}),
                "decision": "HUMAN_REVIEW_NO_AUTO_STATE_CHANGE",
            })
    uk_related = []
    for ocid, release_ids in procedures.items():
        if len(release_ids) > 1:
            uk_related.append({"ocid_sha256": _hash_identifier("uk_fts", "ocid", ocid),
                               "release_count": len(release_ids),
                               "decision": "SAME_PROCEDURE_CANDIDATE_ONLY"})
    multi_scope.sort(key=lambda x: x["identity_sha256"])
    uk_related.sort(key=lambda x: x["ocid_sha256"])
    return {
        "contract_version": "bohun-review-gate-r3-v1",
        "mode": "OFFLINE_REVIEW_ONLY",
        "qa": "HOLD", "public_release": False,
        "db_write_allowed": False, "publication_allowed": False,
        "scope_count": len(scopes),
        "source_systems": sorted({source for source, _ in scopes}),
        "input_sightings": sum(len(obs) for obs in sightings.values()),
        "distinct_source_external_ids": len(sightings),
        "canada_cross_scope_candidates": [x for x in multi_scope if x["source_system"] == "canadabuys"],
        "uk_multi_release_procedure_candidates": uk_related,
        "metadata_receipts": sorted(coverage, key=lambda x: (x["source_system"], x["collection_scope"])),
        "warnings": [
            "No verified live API wire formats or license clearance",
            "Absence from snapshot never proves closure or cancellation",
            "HOLD/UNVERIFIED never becomes an approval or publication",
            "UK releases sharing OCID are not merged or adjudicated",
            "Canada awards not handled until real schema independently verified",
            "EU single-page diagnostic is not complete EDF coverage",
            "No EXP-009 intake_batches or intelligence table writes",
        ],
    }
