"""Immutable typed envelope and blocked proposals; no target writers exist."""
from .adapters import adapt, profile
from .contracts import (VERSION, digest, hash_value, parse_json, producer_identity, require,
                        sha256, timestamp)

ARTIFACT_KEYS = set("""schema_name schema_version artifact_id source_slot_id profile_ref profile_hash
source_namespace producer_version producer_git_sha producer_code_hash producer_tree_state produced_at
query_fingerprint scope collection_mode input_manifest input_manifest_hash source_response_hashes record_count
output_hashes retrieval_status source_reported_counts coverage_status errors limitations records assessments
row_outcomes counts collision_keys public_release""".split())


def build_artifact(input_bytes, adapter, projection, producer, observed_at):
    producer = producer_identity(producer)
    timestamp(observed_at)
    require(projection.get("schema_name") == "bohun-planning-projection" and
            projection.get("schema_version") == VERSION, "PROJECTION_VERSION")
    master_hash = hash_value(projection["master_sha256"])
    source_profile = profile(adapter, master_hash)
    slots = [s for s in projection["slots"] if s["slot_id"] == source_profile["source_slot_id"]]
    require(len(slots) == 1 and slots[0]["source_profile_label"] == source_profile["publisher"],
            "SOURCE_SLOT_MISMATCH")
    mapped = adapt(parse_json(input_bytes), adapter, source_profile, observed_at)
    input_manifest = {"input_sha256": sha256(input_bytes), "profile_hash": digest(source_profile),
                      "schema_ref": f"legacy-{adapter}-report-v1"}
    artifact = {"schema_name": "bohun-source-artifact", "schema_version": VERSION,
                "artifact_id": digest([input_manifest, producer, observed_at]),
                "source_slot_id": source_profile["source_slot_id"],
                "profile_ref": source_profile["profile_ref"], "profile_hash": digest(source_profile),
                "source_namespace": source_profile["source_namespace"],
                "producer_version": producer["version"], "producer_git_sha": producer["git_sha"],
                "producer_code_hash": producer["code_hash"], "producer_tree_state": producer["tree_state"],
                "produced_at": observed_at, "query_fingerprint": digest([source_profile, mapped["scope"]]),
                "scope": mapped["scope"], "collection_mode": "OFFLINE_REPORT_PROJECTION",
                "input_manifest": input_manifest, "input_manifest_hash": digest(input_manifest),
                "source_response_hashes": [], "record_count": len(mapped["records"]),
                "output_hashes": {"canonical_records": digest(mapped["records"])},
                "retrieval_status": mapped["retrieval_status"],
                "source_reported_counts": mapped["source_reported_counts"],
                "coverage_status": mapped["coverage_status"], "errors": mapped["errors"],
                "limitations": mapped["limitations"], "records": mapped["records"],
                "assessments": [{"dimension": "structural", "status": "HOLD" if
                                 mapped["counts"]["quarantined"] or mapped["collision_keys"] else "PASS"},
                                {"dimension": "evidence_rights_relevance_coverage", "status": "HOLD"}],
                "row_outcomes": mapped["row_outcomes"], "counts": mapped["counts"],
                "collision_keys": mapped["collision_keys"], "public_release": False}
    return artifact


def destination_proposals(artifact):
    require(isinstance(artifact, dict) and set(artifact) == ARTIFACT_KEYS, "ARTIFACT_FIELDS")
    require(artifact.get("schema_name") == "bohun-source-artifact" and
            artifact.get("schema_version") == VERSION, "ARTIFACT_VERSION")
    artifact_hash = digest(artifact)
    return [{"source_artifact_id": artifact["artifact_id"], "source_artifact_hash": artifact_hash,
             "source_artifact_version": VERSION, "record_ref": r["record_ref"],
             "logical_destination": "opportunity_candidate" if r["record_kind"] == "OPPORTUNITY"
             else "knowledge_candidate", "mapping_profile_ref": None, "target_contract_ref": None,
             "operation": "NO_WRITE", "eligible_for_future_review": False,
             "blocked_reasons": ["DESTINATION_NOT_ADMITTED", "RIGHTS_UNKNOWN", "RELEVANCE_UNVERIFIED",
                                 "COVERAGE_UNVERIFIED", "SOURCE_BYTES_NOT_AVAILABLE", "CONTENT_QA_REQUIRED"],
             "public_release": False} for r in artifact["records"]]
