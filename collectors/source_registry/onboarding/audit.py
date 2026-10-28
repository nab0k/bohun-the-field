"""Build and validate a local review bundle. Manifest is the commit point."""
import argparse
import json
import os
from pathlib import Path
import tempfile

from .artifact import ARTIFACT_KEYS, build_artifact, destination_proposals
from .contracts import (ContractError, VERSION, canonical, digest, parse_json, producer_identity,
                        read_bytes, require, safe_path, sha256, timestamp)
from .registry import project_markdown, validate_projection

PRIVATE_ROOT = Path(__file__).parents[5] / "local-runtime/experiments/EXP-009/source-onboarding"
FILES = ("registry_projection.json", "records.jsonl", "audit_report.json", "destination_proposals.jsonl")


def _json_bytes(value):
    return canonical(value) + b"\n"


def _jsonl(rows):
    return b"".join(_json_bytes(row) for row in rows)


def prepare_bundle(master, expected_hash, inputs, producer, observed_at):
    """Pure function: explicitly supplied bytes only. Inputs must be synthetic in this stage."""
    require(isinstance(inputs, dict) and set(inputs) == {"grants", "openalex"}, "INPUT_ADAPTER_SET")
    producer_identity(producer)
    timestamp(observed_at)
    projection = project_markdown(master, expected_hash)
    artifacts = [build_artifact(inputs[name], name, projection, producer, observed_at)
                 for name in ("grants", "openalex")]
    records = [r for a in artifacts for r in a["records"]]
    proposals = [p for a in artifacts for p in destination_proposals(a)]
    report = {"schema_name": "bohun-source-audit", "schema_version": VERSION,
              "label": "SYNTHETIC/NO_EXTERNAL_WRITES", "status": "HOLD", "public_release": False,
              "artifacts": artifacts, "candidate_count": len(records), "proposal_count": len(proposals),
              "live_access": "NOT_RUN", "checkpoint": "NOT_OWNED_NO_ADVANCEMENT"}
    payloads = dict(zip(FILES, (_json_bytes(projection), _jsonl(records), _json_bytes(report), _jsonl(proposals))))
    manifest = {"schema_name": "bohun-onboarding-run", "schema_version": VERSION,
                "label": "SYNTHETIC/NO_EXTERNAL_WRITES", "producer": producer, "observed_at": observed_at,
                "master_sha256": expected_hash, "inputs": {k: sha256(v) for k, v in inputs.items()},
                "files": {k: sha256(v) for k, v in payloads.items()}, "public_release": False}
    result = {**payloads, "run_manifest.json": _json_bytes(manifest)}
    validate_bundle(result)
    return result


def _decode(data):
    # Outputs can contain both bounded input reports; their bound differs from an input file.
    return parse_json(data, limit=12 * 1024 * 1024)


def validate_bundle(payloads):
    require(set(payloads) == {*FILES, "run_manifest.json"}, "BUNDLE_FILES")
    manifest = _decode(payloads["run_manifest.json"])
    require(isinstance(manifest, dict) and set(manifest) == {"schema_name", "schema_version", "label",
            "producer", "observed_at", "master_sha256", "inputs", "files", "public_release"}, "MANIFEST_FIELDS")
    require(manifest.get("schema_name") == "bohun-onboarding-run" and
            manifest.get("schema_version") == VERSION and manifest.get("public_release") is False,
            "MANIFEST_CONTRACT")
    require(manifest.get("label") == "SYNTHETIC/NO_EXTERNAL_WRITES", "MANIFEST_CONTRACT")
    producer_identity(manifest.get("producer"))
    timestamp(manifest.get("observed_at"))
    require(manifest.get("files") == {k: sha256(payloads[k]) for k in FILES}, "MANIFEST_MISMATCH")
    report = _decode(payloads["audit_report.json"])
    projection = _decode(payloads["registry_projection.json"])
    validate_projection(projection)
    require(projection.get("master_sha256") == manifest.get("master_sha256"), "MASTER_CHANGED")
    rows = {}
    for key in ("records.jsonl", "destination_proposals.jsonl"):
        raw = payloads[key]
        require(not raw or raw.endswith(b"\n"), "INCOMPLETE_JSONL")
        rows[key] = [_decode(line) for line in raw.splitlines()]
    records, proposals = rows["records.jsonl"], rows["destination_proposals.jsonl"]
    require(isinstance(report, dict) and set(report) == {"schema_name", "schema_version", "label", "status",
            "public_release", "artifacts", "candidate_count", "proposal_count", "live_access", "checkpoint"},
            "AUDIT_FIELDS")
    require(report.get("schema_name") == "bohun-source-audit" and report.get("schema_version") == VERSION and report.get("status") == "HOLD" and
            report.get("public_release") is False, "AUDIT_CONTRACT")
    require(report.get("label") == "SYNTHETIC/NO_EXTERNAL_WRITES" and report.get("live_access") == "NOT_RUN"
            and report.get("checkpoint") == "NOT_OWNED_NO_ADVANCEMENT", "AUDIT_CONTRACT")
    artifacts = report.get("artifacts")
    require(isinstance(artifacts, list) and len(artifacts) == 2, "AUDIT_CONTRACT")
    expected_records, expected_proposals = [], []
    for a in artifacts:
        require(isinstance(a, dict) and set(a) == ARTIFACT_KEYS and isinstance(a.get("records"), list),
                "ARTIFACT_FIELDS")
        require(a.get("schema_name") == "bohun-source-artifact" and a.get("schema_version") == VERSION
                and a.get("public_release") is False and a.get("coverage_status") == "UNVERIFIED"
                and a.get("source_response_hashes") == [], "ARTIFACT_CONTRACT")
        require(a["retrieval_status"] in {"UNKNOWN", "PARTIAL", "FAILED", "EMPTY"}, "RETRIEVAL_STATUS")
        require(a.get("record_count") == len(a["records"]) and
                a.get("output_hashes") == {"canonical_records": digest(a["records"])}, "ARTIFACT_RECORDS")
        require(a.get("input_manifest_hash") == digest(a["input_manifest"]), "INPUT_MANIFEST_MISMATCH")
        for record in a["records"]:
            require(isinstance(record, dict), "RECORD_CONTRACT")
            require(record.get("public_release") is False and record.get("evidence_qa") == "HOLD"
                    and record.get("rights") == "UNKNOWN" and record.get("sensitivity") == "UNKNOWN",
                    "RECORD_GATE")
        expected_records.extend(a["records"])
        expected_proposals.extend(destination_proposals(a))
    require(records == expected_records and proposals == expected_proposals, "BUNDLE_CROSS_REFERENCE")
    require(report.get("candidate_count") == len(records) and report.get("proposal_count") == len(proposals),
            "BUNDLE_COUNTS")
    return manifest


def write_bundle(output, payloads, *, private_root):
    """Reserve a NEW directory; publish exclusive files, manifest last. Never overwrite.

    A crash may leave an incomplete directory without a manifest. It is not a successful run
    and cannot be reused. Stage validation happens before reservation/publication.
    """
    root, target = safe_path(private_root), safe_path(output)
    require(root.is_dir() and target.parent == root and target.name not in ("", ".", ".."), "OUTPUT_SCOPE")
    require(not ({"public", "dist", ".git", "repos"} & set(root.parts)), "OUTPUT_SCOPE")
    require(not target.exists(), "OUTPUT_EXISTS")
    validate_bundle(payloads)
    try:
        with tempfile.TemporaryDirectory(prefix=".onboarding-stage-", dir=root) as stage:
            stage = Path(stage)
            for name, data in payloads.items():
                p = stage / name
                with p.open("xb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            validate_bundle({name: (stage / name).read_bytes() for name in payloads})
            target.mkdir(mode=0o700)  # Exclusive reservation, including against concurrent writers.
            for name in (*FILES, "run_manifest.json"):
                # Hard-link publication is atomic and fails if ANY destination already exists.
                # Unlike POSIX rename, it cannot replace an existing file or empty directory.
                os.link(stage / name, target / name, follow_symlinks=False)
        return sha256(payloads["run_manifest.json"])
    except OSError as exc:
        raise ContractError("OUTPUT_IO_INCOMPLETE") from exc


def read_bundle(directory):
    directory = safe_path(directory)
    require(directory.is_dir(), "BUNDLE_DIRECTORY")
    require({p.name for p in directory.iterdir()} == {*FILES, "run_manifest.json"}, "INCOMPLETE_BUNDLE")
    payloads = {}
    for name in (*FILES, "run_manifest.json"):
        p = safe_path(directory / name)
        payloads[name] = read_bytes(p, limit=12 * 1024 * 1024)
    return validate_bundle(payloads)


def main(argv=None, *, permitted_root=None):
    parser = argparse.ArgumentParser(description="Offline synthetic source audit. No network or destination writes.")
    parser.add_argument("--master", required=True)
    parser.add_argument("--master-sha256", required=True)
    parser.add_argument("--grants", required=True)
    parser.add_argument("--openalex", required=True)
    parser.add_argument("--producer-git-sha", required=True)
    parser.add_argument("--producer-code-hash", required=True)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--synthetic", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        producer = {"version": VERSION, "git_sha": args.producer_git_sha,
                    "code_hash": args.producer_code_hash, "tree_state": "DIRTY_ISOLATED"}
        payloads = prepare_bundle(read_bytes(args.master), args.master_sha256,
                                 {"grants": read_bytes(args.grants), "openalex": read_bytes(args.openalex)},
                                 producer, args.observed_at)
        h = write_bundle(args.output, payloads, private_root=PRIVATE_ROOT if permitted_root is None else permitted_root)
        print(json.dumps({"status": "SYNTHETIC_REVIEW_ONLY", "manifest_sha256": h, "public_release": False}))
        return 0
    except (ContractError, OSError) as exc:
        print(json.dumps({"status": "STOP", "reason": exc.code if isinstance(exc, ContractError) else "LOCAL_IO"}))
        return 2
