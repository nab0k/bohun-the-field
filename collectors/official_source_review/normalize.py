"""Pure offline UK/Canada/EU candidate normalization for review only.

Never assert procurement, award or funding as independently verified.
Do not save source bodies, create database rows or publish results.
"""
import csv
import hashlib
import io
import json


class NormalizationError(ValueError):
    pass


def _value(v, cap=2000):
    if v is None:
        return ""
    if not isinstance(v, (str, int, float)):
        raise NormalizationError("non_scalar_field")
    return str(v).strip()[:cap]


def _hash(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _candidate(source, external_id, title, kind, raw):
    external_id, title = _value(external_id, 512), _value(title)
    if not external_id or not title:
        raise NormalizationError("missing_id_or_title")
    return {
        "source_system": source,
        "external_id": external_id,
        "title": title,
        "item_type_candidate": kind,
        "source_record_sha256": _hash(raw),
        "capture_status": "METADATA_ONLY",
        "verification_status": "HOLD",
        "relevance_status": "UNVERIFIED",
        "public_release": False,
        "source_metadata": {
            "source_system": source, "source_format": "PUBLIC_METADATA",
        },
    }


def uk_release(release):
    if not isinstance(release, dict):
        raise NormalizationError("uk_release_not_object")
    ocid, rid = _value(release.get("ocid"), 512), _value(release.get("id"), 256)
    if not ocid or not rid:
        raise NormalizationError("uk_missing_ocid_or_id")
    tender = release.get("tender") or {}
    if not isinstance(tender, dict):
        raise NormalizationError("uk_bad_tender")
    tags = release.get("tag") or []
    if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags):
        raise NormalizationError("uk_bad_tags")
    awards = release.get("awards") or []
    if not isinstance(awards, list):
        raise NormalizationError("uk_bad_awards")
    award_title = next(
        (a.get("title") for a in awards
         if isinstance(a, dict) and isinstance(a.get("title"), str)), None
    )
    title = tender.get("title") or award_title
    kind = ("contract_award_notice" if "award" in tags else
            "procurement_notice" if "tender" in tags else
            "procurement_other_release")
    return _candidate("uk_fts", ocid + "::" + rid, title, kind, release)


def canada_row(row, *, dataset):
    allowed = {"ca_new", "ca_open"}
    if dataset not in allowed or not isinstance(row, dict):
        raise NormalizationError("ca_invalid_dataset_or_row")
    external_id = row.get("referenceNumber-numeroReference")
    title = row.get("title-titre-eng") or row.get("title-titre-fra")
    return _candidate("canadabuys", external_id, title, "procurement_notice", row)


def eu_result(row):
    if not isinstance(row, dict):
        raise NormalizationError("eu_not_object")
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise NormalizationError("eu_bad_metadata")
    title = metadata.get("title") or row.get("title")
    if isinstance(title, list):
        title = title[0] if title else None
    code = metadata.get("type") or []
    if isinstance(code, list):
        code = code[0] if code else None
    kind = ("grant_call_candidate" if str(code) in ("1", "2", "8") else
            "procurement_notice" if str(code) == "0" else "eu_unclassified")
    return _candidate("eu_funding", row.get("reference"), title, kind, row)


def prepare(source, data, *, scope):
    """Stable IDs, fail-closed conflicts, no cross-scope comparison.

    data: in-memory parsed rows or source response, never remote I/O.
    """
    if not scope or not isinstance(scope, str):
        raise NormalizationError("scope_required")
    if source == "uk_fts":
        if not isinstance(data, dict) or not isinstance(data.get("releases"), list):
            raise NormalizationError("uk_releases_required")
        if (data.get("links") or {}).get("next"):
            raise NormalizationError("uk_incomplete_page")
        rows, convert = data["releases"], uk_release
    elif source == "canadabuys":
        if scope not in {"ca_new", "ca_open"}:
            raise NormalizationError("ca_scope_required")
        if isinstance(data, str):
            reader = csv.DictReader(io.StringIO(data.lstrip("\ufeff")))
            if not reader.fieldnames:
                raise NormalizationError("ca_missing_header")
            rows = list(reader)
            if any(None in row for row in rows):
                raise NormalizationError("ca_ragged_csv")
        elif isinstance(data, list):
            rows = data
        else:
            raise NormalizationError("ca_rows_required")
        convert = lambda row: canada_row(row, dataset=scope)
    elif source == "eu_funding":
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise NormalizationError("eu_results_required")
        total = data.get("totalResults")
        if isinstance(total, int) and total > len(data["results"]):
            raise NormalizationError("eu_incomplete_search")
        rows, convert = data["results"], eu_result
    else:
        raise NormalizationError("unsupported_source")
    if len(rows) > 20000:
        raise NormalizationError("too_many_rows")
    by_id = {}
    for row in rows:
        item = convert(row)
        key = item["external_id"]
        prior = by_id.get(key)
        if prior is not None and prior["source_record_sha256"] != item["source_record_sha256"]:
            raise NormalizationError("conflicting_versions_same_id")
        by_id[key] = item
    return {
        "contract_version": "bohun-source-review-v1",
        "source_system": source, "collection_scope": scope,
        "status": "PREPARED_UNVERIFIED", "qa": "HOLD",
        "public_release": False, "items": list(by_id.values()),
    }
