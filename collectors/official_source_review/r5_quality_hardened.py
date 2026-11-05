"""Offline R5.1 quality gate. Pure, metadata-only, fails closed.

Rows passed here remain in memory and are NOT persisted. The caller controls
the bounded read-only source transport; this module never uses the network.
"""
from collections import Counter

import normalize

ALLOWED_SOURCES = frozenset(("uk", "ca_new", "ca_open", "eu"))
ALLOWED_KINDS = frozenset(("procurement_notice", "contract_award_notice",
                           "procurement_other_release", "grant_call_candidate",
                           "eu_unclassified"))
KNOWN_REJECTION_CODES = frozenset((
    "missing_id_or_title", "uk_missing_ocid_or_id", "uk_bad_tender",
    "uk_bad_tags", "uk_bad_awards", "non_scalar_field", "eu_bad_metadata",
    "eu_not_object", "ca_invalid_dataset_or_row", "uk_release_not_object",
))


def _stable_identity(source, row):
    """Validate identity before normalize._candidate silently truncates at 512 chars."""
    if not isinstance(row, dict):
        return "invalid_record_shape"
    if source == "uk":
        ocid, release_id = row.get("ocid"), row.get("id")
        if (not isinstance(ocid, (str, int, float)) or
                not isinstance(release_id, (str, int, float))):
            return "invalid_identity"
        ocid, release_id = str(ocid).strip(), str(release_id).strip()
        if not ocid or not release_id:
            return "invalid_identity"
        if (len(ocid) > 512 or len(release_id) > 256 or
                len(ocid) + len(release_id) + 2 > 512):
            return "identity_overlong"
        return None
    if source in ("ca_new", "ca_open"):
        ident = row.get("referenceNumber-numeroReference")
    else:
        ident = row.get("reference")
    if ident is None or not isinstance(ident, (str, int, float)):
        return "invalid_identity"
    ident = str(ident).strip()
    if not ident:
        return "invalid_identity"
    if len(ident) > 512:
        return "identity_overlong"
    return None


def quality_report(source, rows, *, source_complete, reported_total=None):
    """Aggregate safe reason codes; never return row data, URLs, IDs or hashes."""
    if source not in ALLOWED_SOURCES:
        raise ValueError("unsupported_source")
    if type(source_complete) is not bool:
        raise ValueError("source_complete_must_be_bool")

    converter = (normalize.uk_release if source == "uk" else
                 (lambda row: normalize.canada_row(row, dataset=source))
                 if source in ("ca_new", "ca_open") else normalize.eu_result)
    seen = {}
    reasons = Counter()
    kinds = Counter()
    total = duplicate = conflicts = 0
    for row in rows:
        total += 1
        reason = _stable_identity(source, row)
        if reason:
            reasons[reason] += 1
            continue
        try:
            item = converter(row)
            if (item.get("verification_status") != "HOLD" or
                    item.get("relevance_status") != "UNVERIFIED" or
                    item.get("public_release") is not False or
                    item.get("capture_status") != "METADATA_ONLY"):
                reasons["unsafe_candidate_state"] += 1
                continue
            kind = item.get("item_type_candidate")
            if kind not in ALLOWED_KINDS:
                reasons["unrecognized_candidate_type"] += 1
                continue
            identity = item["external_id"]
            digest = item["source_record_sha256"]
            if identity not in seen:
                seen[identity] = digest
                kinds[kind] += 1
            elif seen[identity] == digest:
                duplicate += 1
            else:
                conflicts += 1
        except normalize.NormalizationError as exc:
            code = str(exc)
            reasons[code if code in KNOWN_REJECTION_CODES else "normalization_error"] += 1
        except Exception:
            reasons["unexpected_normalization_error"] += 1

    rejected_rows = sum(reasons.values())
    reported = reported_total if type(reported_total) is int and reported_total >= 0 else None
    if reported is not None and reported < total:
        reasons["reported_total_inconsistent"] += 1
    issue_count = sum(reasons.values())
    review_required = bool(issue_count or conflicts or not source_complete)
    return {
        "contract": "bohun-r5.1-quality-review-v1", "source": source,
        "qa": "HOLD", "public_release": False, "ready_for_import": False,
        "defence_relevance_verified": 0,
        "source_snapshot_complete": source_complete,
        "complete": source_complete,
        "sample_only": not source_complete,
        "records_seen": total,
        "unique_candidates": len(seen),
        "duplicate_observations": duplicate,
        "conflicting_versions": conflicts,
        "rejected_rows": rejected_rows,
        "rejected": rejected_rows,
        "rejection_reasons": dict(sorted(reasons.items())),
        "candidate_kinds": dict(sorted(kinds.items())),
        "reported_total": reported,
        "quality_gate": "REVIEW_REQUIRED" if review_required else "NO_ROW_ERRORS_DETECTED",
        "note": "No row-level semantic or defence relevance verification; no import approval.",
    }
