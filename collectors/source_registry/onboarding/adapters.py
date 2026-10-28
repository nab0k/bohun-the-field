"""Pure mappings of two existing report formats. Never import the collectors."""
import re

from .contracts import (MAX_BYTES, MAX_ROWS, MAX_ROW_BYTES, VERSION, canonical, digest, hash_value,
                        presence, require, timestamp)

FIELDS = {
    "grants": {"external_id": str, "title": str, "opportunity_number": str, "agency": str,
               "agency_code": str, "status": str, "open_date": str, "close_date": str,
               "source": str, "matched_keywords": list},
    "openalex": {"external_id": str, "doi": str, "title": str, "publication_date": str,
                 "source_url": str, "publisher_source": str, "signal_type": str,
                 "discovery_query": str, "source_system": str, "is_verified_relevance": bool},
}
TOP = {
    "grants": {"source", "kind", "ingestion_status", "keyword_count", "unique_results", "errors", "items"},
    "openalex": {"source", "since", "queries", "unique_candidates", "errors", "note", "items"},
}


def semantic_payload(row):
    # Discovery evidence is not a revision of the underlying opportunity/work.
    return {k: v for k, v in row.items() if k not in {"matched_keywords", "discovery_query"}}


def profile(adapter, master_hash):
    require(adapter in FIELDS, "UNSUPPORTED_ADAPTER")
    hash_value(master_hash)
    return {"profile_ref": f"offline-{adapter}-report-v1", "profile_version": VERSION,
            "publisher": "Grants.gov" if adapter == "grants" else "OpenAlex",
            "index": "search2" if adapter == "grants" else "works", "language": "UNSPECIFIED",
            "programme_filter": "legacy_keyword_sample", "record_kind": "OPPORTUNITY" if adapter == "grants"
            else "SCHOLARLY_METADATA", "source_namespace": "grants.gov" if adapter == "grants" else "openalex",
            "source_slot_id": "S04" if adapter == "grants" else "S34", "master_hash": master_hash,
            "reviewed_reference": "SOURCE_CONNECTION_DESIGN_v1/G3", "admission": "OFFLINE_ONLY"}


def _date(raw):
    return {"raw": raw, "value": None, "precision": "UNKNOWN", "timezone": "UNKNOWN"}


def adapt(report, adapter, source_profile, observed_at):
    """Return records, every input-row outcome, source errors and known scope limits."""
    require(adapter in FIELDS, "UNSUPPORTED_ADAPTER")
    require(isinstance(source_profile, dict), "PROFILE_MISMATCH")
    require(source_profile == profile(adapter, source_profile.get("master_hash")), "PROFILE_MISMATCH")
    timestamp(observed_at)
    require(isinstance(report, dict) and set(report) == TOP[adapter], "INVALID_REPORT")
    require(len(canonical(report)) <= MAX_BYTES, "INPUT_SIZE")
    require(isinstance(report["items"], list) and len(report["items"]) <= MAX_ROWS, "REPORT_ROWS")
    require(report["source"] == source_profile["publisher"], "REPORT_SOURCE")
    errors = report["errors"]
    if adapter == "grants":
        require(isinstance(errors, dict) and all(isinstance(k, str) and isinstance(v, str)
                                               for k, v in errors.items()), "INVALID_ERRORS")
        require(report["kind"] == "grant_opportunity" and report["ingestion_status"] ==
                "read_only_artifact_not_database", "REPORT_CONTRACT")
        query_count, count = report["keyword_count"], report["unique_results"]
        scope = {"keyword_count": query_count, "keywords": None, "keyword_list_status": "NOT_RECORDED",
                 "rows_per_query": None, "page_selection": "LEGACY_BOUNDED_SEARCH"}
    else:
        require(isinstance(errors, list) and all(isinstance(e, dict) and set(e) == {"query", "error_type"}
                and all(isinstance(v, str) for v in e.values()) for e in errors), "INVALID_ERRORS")
        require(isinstance(report["since"], str) and isinstance(report["note"], str), "REPORT_CONTRACT")
        query_count, count = report["queries"], report["unique_candidates"]
        scope = {"query_count": query_count, "since": report["since"], "note": report["note"],
                 "query_list": None, "page_selection": "FIRST_PAGE_ONLY", "per_page": None}
    require(type(query_count) is int and query_count > 0 and type(count) is int and count >= 0,
            "INVALID_COUNTS")
    limitations = ["SOURCE_BYTES_NOT_AVAILABLE", "UPSTREAM_IDENTITY_UNKNOWN", "METADATA_ONLY",
                   "KEYWORD_SAMPLE_NOT_MARKET_ENUMERATION", "COVERAGE_UNVERIFIED", "RIGHTS_UNKNOWN",
                   "RELEVANCE_UNVERIFIED", "NO_VERIFIED_SOURCE_REVISION", "QUERY_SPEC_NOT_RECORDED"]
    if count != len(report["items"]):
        limitations.append("SOURCE_COUNT_MISMATCH")
    records, outcomes, by_key, collisions = [], [], {}, set()
    for index, row in enumerate(report["items"]):
        require(len(canonical(row)) <= MAX_ROW_BYTES, "ROW_SIZE")
        raw_hash = digest(row)
        if not isinstance(row, dict):
            outcomes.append({"input_index": index, "status": "QUARANTINED", "reasons": ["ROW_NOT_OBJECT"],
                             "input_row_hash": raw_hash})
            continue
        states = presence(row, FIELDS[adapter])
        invalid = [k for k, v in states.items() if v == "INVALID"]
        if "matched_keywords" in row and isinstance(row["matched_keywords"], list):
            if not all(isinstance(v, str) for v in row["matched_keywords"]):
                invalid.append("matched_keywords")
                states["matched_keywords"] = "INVALID"
        reasons = ["INVALID_FIELD:" + k for k in invalid]
        if set(row) - set(FIELDS[adapter]):
            reasons.append("UNKNOWN_FIELD")
        native = row.get("external_id")
        pattern = r"[0-9]+" if adapter == "grants" else r"https://openalex\.org/W[0-9]+"
        if not isinstance(native, str) or not re.fullmatch(pattern, native):
            reasons.append("INVALID_NATIVE_ID")
        if reasons:
            outcomes.append({"input_index": index, "status": "QUARANTINED", "reasons": reasons,
                             "input_row_hash": raw_hash, "field_presence": states})
            continue
        key = source_profile["source_namespace"] + ":" + native
        semantic = digest({"namespace": source_profile["source_namespace"], "native_id": native,
                           "payload": semantic_payload(row)})
        identity = digest([key, semantic])
        discovery = {"input_index": index, "input_row_hash": raw_hash,
                     "matched_keywords": row.get("matched_keywords"), "discovery_query": row.get("discovery_query")}
        if (key, semantic) in by_key:
            by_key[key, semantic]["provenance"]["discovery_observations"].append(discovery)
            outcomes.append({"input_index": index, "status": "DUPLICATE", "record_ref": identity,
                             "input_row_hash": raw_hash})
            continue
        if any(k == key for k, _ in by_key):
            collisions.add(key)
        issues = ["MISSING_TITLE"] if states["title"] != "PRESENT" else []
        record = {"record_ref": identity, "entity_key": key, "semantic_hash": semantic,
                  "record_kind": source_profile["record_kind"], "namespace": source_profile["source_namespace"],
                  "native_id": native, "native_version": None, "version_kind": "SEMANTIC_OBSERVATION",
                  "source_url": row.get("source_url"), "publisher": source_profile["publisher"],
                  "observed_at": observed_at, "source_time": _date(row.get("open_date") if adapter == "grants"
                                                                 else row.get("publication_date")),
                  "payload": row, "field_presence": states,
                  "provenance": {"profile_ref": source_profile["profile_ref"], "profile_hash": digest(source_profile),
                                 "input_index": index, "input_row_hash": raw_hash,
                                 "discovery_observations": [discovery],
                                 "upstream_identity": {"status": "UNKNOWN", "reason": "NOT_RECORDED"},
                                 "content_extent": "METADATA_ONLY"},
                  "structural_qa": {"status": "HOLD" if issues else "PASS", "reasons": issues},
                  "evidence_qa": "HOLD", "relevance": "UNVERIFIED", "sensitivity": "UNKNOWN",
                  "rights": "UNKNOWN", "public_release": False}
        records.append(record)
        by_key[key, semantic] = record
        outcomes.append({"input_index": index, "status": "CANDIDATE", "record_ref": identity,
                         "input_row_hash": raw_hash})
    for record in records:
        if record["entity_key"] in collisions:
            record["structural_qa"] = {"status": "HOLD", "reasons": ["KEY_COLLISION"]}
    for outcome in outcomes:
        if outcome.get("record_ref") in {r["record_ref"] for r in records if r["entity_key"] in collisions}:
            outcome["collision"] = True
    return {"profile": source_profile, "records": records, "row_outcomes": outcomes, "scope": scope,
            "errors": errors, "limitations": limitations, "source_reported_counts": count,
            "retrieval_status": "FAILED" if errors and not report["items"] else "PARTIAL" if errors
            else "EMPTY" if not report["items"] else "UNKNOWN",
            "coverage_status": "UNVERIFIED", "collision_keys": sorted(collisions),
            "counts": {"input_occurrences": len(outcomes), "unique_keyed_candidates": len(records),
                       "unique_entity_keys": len({r["entity_key"] for r in records}),
                       "duplicates": sum(o["status"] == "DUPLICATE" for o in outcomes),
                       "quarantined": sum(o["status"] == "QUARANTINED" for o in outcomes)}}
