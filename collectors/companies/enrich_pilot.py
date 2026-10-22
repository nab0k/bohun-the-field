"""DISCOVERY-ENRICH-01: bounded, read-only, review-only identity screening.

Part of the existing EXP-012 companies collector, not a second ingestion system.
No writes to Supabase or existing candidate/company status, no publication.
Run as: python -m collectors.companies.enrich_pilot --explain
"""
import argparse
import hashlib
import json
import os
import pathlib
from urllib.parse import quote

from .sources.gleif import Gleif
from .sources.http import JsonHttp

SOURCE = "eurosatory_2024_exhibitors"
HERE = pathlib.Path(__file__).resolve().parent
MANIFEST = HERE / "data" / "discovery_enrich01_cohort.json"
REPO_ROOT = HERE.parents[1]
SIZES = {"discovered": 50, "candidate_existing": 20, "hold": 11}


def load_manifest():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("source") != SOURCE:
        raise ValueError("unexpected cohort source")
    groups = manifest["groups"]
    if {key: len(ids) for key, ids in groups.items()} != SIZES:
        raise ValueError("cohort group sizes changed")
    all_ids = [n for ids in groups.values() for n in ids]
    if len(all_ids) != len(set(all_ids)) or len(all_ids) != 81:
        raise ValueError("duplicate or incomplete frozen cohort")
    for group, ids in groups.items():
        expected = manifest["sample_group_id_hashes_md5"][group]
        value = hashlib.md5(",".join(map(str, sorted(ids))).encode()).hexdigest()
        if value != expected:
            raise ValueError("frozen cohort checksum mismatch")
    return manifest


def validate_rows(rows, manifest):
    expected = {int(item): status for status, items in manifest["groups"].items() for item in items}
    if len(rows) != 81 or {int(r["id"]) for r in rows} != set(expected):
        raise ValueError("Supabase cohort differs from frozen manifest")
    ambiguous_options = 0
    for row in rows:
        if row.get("source") != SOURCE or row.get("status") != expected[int(row["id"])]:
            raise ValueError("candidate source or status changed")
        if not row.get("name") or not row.get("source_key"):
            raise ValueError("missing source-backed candidate name/key")
        if row["status"] == "candidate_existing" and not row.get("matched_company_id"):
            raise ValueError("matched candidate has no company id")
        if row["status"] == "hold":
            options = int(row.get("possible_matches") or 0)
            if options < 2:
                raise ValueError("HOLD candidate has lost alternative matches")
            ambiguous_options += options
    if ambiguous_options != manifest["baseline"]["ambiguous_match_options"]:
        raise ValueError("HOLD alternatives changed; stop and review")
    return True


QUERY = """SELECT d.id,d.name,d.status,d.reported_country,
 d.website AS discovery_website,d.matched_company_id,d.match_reason,
 o.source,o.source_key,c.name AS existing_name,c.website AS existing_website,
 COALESCE((SELECT json_agg(json_build_object('scheme',i.scheme,'value',i.value))
           FROM companies.company_identifier i WHERE i.company_id=c.id),'[]'::json)
 AS existing_identifiers,
 (SELECT COUNT(*) FROM companies.match_candidate mc
    WHERE mc.observation_id=d.observation_id) AS possible_matches
 FROM companies.discovery_candidate d
 JOIN companies.observation o ON o.id=d.observation_id
 LEFT JOIN companies.company c ON c.id=d.matched_company_id
 WHERE d.id=ANY(%s) AND o.source=%s ORDER BY d.id"""


def read_cohort_from_db(manifest):
    """Connection is explicitly READ ONLY; this function performs only SELECT."""
    from .pg_store import PgStore
    store = PgStore()
    try:
        store.db.execute("SET TRANSACTION READ ONLY")
        ids = [n for a in manifest["groups"].values() for n in a]
        rows = [dict(r) for r in store.db.execute(QUERY, (ids, SOURCE)).fetchall()]
        validate_rows(rows, manifest)
        return rows
    finally:
        store.close()


def registry_probe(row, client):
    """Return an unverified LEI lead; completeness and ambiguity are explicit."""
    try:
        leads, complete = client.find_exact_candidates(row["name"])
        if not complete:
            return {"status": "registry_search_incomplete", "candidate_count_in_partial_pages": len(leads)}
        if not leads:
            return {"status": "no_exact_registry_lead"}
        if len(leads) > 1:
            return {"status": "ambiguous_registry_leads", "candidate_count": len(leads),
                    "registry_urls": [f"https://api.gleif.org/api/v1/lei-records/{quote(item['lei'])}"
                                      for item in leads if item.get("lei")]}
        lead = leads[0]
        lei = lead.get("lei", "")
        if len(lei) != 20 or not lei.isalnum():
            return {"status": "invalid_registry_lead"}
        return {"status": "single_exact_lei_candidate", "lei": lei,
                "legal_name": lead.get("legal_name"), "registry_country": lead.get("country"),
                "entity_status": lead.get("status"),
                "registry_url": f"https://api.gleif.org/api/v1/lei-records/{quote(lei)}",
                "identity_confirmed": False}
    except Exception as exc:
        return {"status": "registry_request_error", "error_type": type(exc).__name__}


def research_record(row, registry=None):
    if row["status"] not in SIZES:
        raise ValueError("unsupported source status")
    return {
        "candidate_id": int(row["id"]), "source": SOURCE, "source_key": row["source_key"],
        "name": row["name"], "reported_country_source_only": row.get("reported_country"),
        "original_status": row["status"], "source_url":
        "https://www.eurosatory.com/en/trends/exhibitors-2024",
        "possible_matches": int(row.get("possible_matches") or 0),
        "existing_company_id_unverified": row.get("matched_company_id"),
        "existing_website_unverified": row.get("existing_website"),
        "existing_identifiers_unverified": row.get("existing_identifiers") or [],
        "registry_lead": registry or {"status": "not_queried"},
        "review_route": ("manual_ambiguity_review" if row["status"] == "hold"
                         else "review_existing_identity" if row["status"] == "candidate_existing"
                         else "collect_independent_evidence"),
        "identity_confirmed": False, "publication_allowed": False,
        "company_creation_allowed": False,
    }


def aggregate(items):
    routes, registry = {}, {}
    for item in items:
        if item["identity_confirmed"] or item["publication_allowed"] or item["company_creation_allowed"]:
            raise ValueError("pilot must never promote a company")
        route = item["review_route"]
        routes[route] = routes.get(route, 0) + 1
        status = item["registry_lead"]["status"]
        registry[status] = registry.get(status, 0) + 1
    return {"pilot": "DISCOVERY-ENRICH-01", "total": len(items),
            "by_route": routes, "registry_results": registry,
            "verified_identities": 0, "new_company_rows": 0,
            "source_country_as_legal_country": 0, "publications": 0}


def prioritized_probe_ids(rows, maximum):
    """2 discovered, 2 potential existing, 1 hold in a five-request smoke test."""
    if maximum < 1 or maximum > 81:
        raise ValueError("bounded GLEIF request count must be 1..81")
    groups = {status: sorted((row for row in rows if row["status"] == status),
                             key=lambda row: int(row["id"])) for status in SIZES}
    ordered = []
    # Stratified round-robin in groups, deterministic and bounded.
    for i in range(max(len(group) for group in groups.values())):
        for status in ("discovered", "candidate_existing", "hold"):
            if i < len(groups[status]):
                ordered.append(int(groups[status][i]["id"]))
    if maximum == 5:
        return set(int(r["id"]) for r in
                   [groups["discovered"][0], groups["discovered"][1],
                    groups["candidate_existing"][0], groups["candidate_existing"][1],
                    groups["hold"][0]])
    return set(ordered[:maximum])


def write_private(records, output_dir):
    output = pathlib.Path(output_dir).expanduser()
    if not output.is_absolute():
        raise ValueError("private output directory must be absolute")
    resolved = output.resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise ValueError("private research data cannot be stored inside Git checkout")
    previous_umask = os.umask(0o077)
    try:
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Restrict permissions even when the directory already existed.
        os.chmod(output, 0o700)
        path = output / "research.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for item in records:
                f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
        os.chmod(path, 0o600)
        report = aggregate(records)
        report_file = output / "aggregate.json"
        report_file.write_text(json.dumps(report, sort_keys=True, indent=2), encoding="utf-8")
        os.chmod(report_file, 0o600)
        return report
    finally:
        os.umask(previous_umask)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--explain", action="store_true")
    p.add_argument("--read-supabase", action="store_true")
    p.add_argument("--input-json", type=pathlib.Path, help="offline fixture path, no network")
    p.add_argument("--gleif", action="store_true", help="opt-in free registry lookups")
    p.add_argument("--max-gleif", type=int, default=0)
    p.add_argument("--private-output-dir", type=pathlib.Path)
    p.add_argument("--review20", action="store_true",
                   help="create 20 private, link-only QA packets; no claim verified")
    p.add_argument("--captured-evidence-manifest", type=pathlib.Path,
                   help="private JSON list of local HTTPS source captures and exact quotes")
    args = p.parse_args(argv)
    manifest = load_manifest()
    if args.explain:
        print(json.dumps({"groups": SIZES, "pilot": manifest["pilot_id"]}, sort_keys=True))
        return 0
    if args.read_supabase == bool(args.input_json):
        p.error("choose exactly one input: --read-supabase or --input-json")
    if not args.private_output_dir:
        p.error("--private-output-dir is required")
    if args.captured_evidence_manifest and not args.review20:
        p.error("--captured-evidence-manifest requires --review20")
    if args.gleif != bool(args.max_gleif):
        p.error("--gleif and --max-gleif must be used together")
    if args.gleif and not 1 <= args.max_gleif <= 81:
        p.error("GLEIF requests must be limited to 1..81")
    rows = (read_cohort_from_db(manifest) if args.read_supabase
            else json.loads(args.input_json.read_text(encoding="utf-8")))
    validate_rows(rows, manifest)
    client = Gleif(JsonHttp(timeout=15, retries=1, backoff=2, pause=0.8)) if args.gleif else None
    to_probe = prioritized_probe_ids(rows, args.max_gleif) if args.gleif else set()
    records = [research_record(row, registry_probe(row, client) if int(row["id"]) in to_probe else None)
               for row in sorted(rows, key=lambda x: int(x["id"]))]
    report = write_private(records, args.private_output_dir)
    if args.review20:
        from . import evidence_review
        packets = evidence_review.review_packets(records)
        report["review20_unverified_packets"] = evidence_review.persist_review_packets(
            args.private_output_dir, packets)
        report["verified_review20_identifications"] = 0
        if args.captured_evidence_manifest:
            manifest_file = args.captured_evidence_manifest.expanduser()
            if not manifest_file.is_absolute():
                p.error("capture manifest must be absolute and outside the repository")
            resolved = manifest_file.resolve()
            if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
                p.error("capture manifest cannot be within the Git checkout")
            captures = json.loads(manifest_file.read_text(encoding="utf-8"))
            if not isinstance(captures, list) or len(captures) > 20:
                p.error("expected a bounded list of at most 20 source captures")
            by_id = {item["candidate_id"]: item for item in packets}
            appended = 0
            for capture in captures:
                case = by_id.get(int(capture["candidate_id"]))
                if case is None:
                    p.error("capture references candidate outside the 20-case pilot")
                snapshot_file = pathlib.Path(capture["snapshot_file"]).expanduser()
                snapshot_path = snapshot_file.resolve()
                if (not snapshot_file.is_absolute() or
                        snapshot_path == REPO_ROOT or REPO_ROOT in snapshot_path.parents or
                        snapshot_file.is_symlink()):
                    p.error("captured source file must be absolute, outside Git and not a symlink")
                # Files are supplied locally by a reviewer; no HTTP is done here.
                with snapshot_file.open("rb") as inp:
                    raw = inp.read(evidence_review.MAX_CAPTURE_BYTES + 1)
                receipt = evidence_review.capture_receipt(
                    case, capture["source_url"], raw, capture["exact_quote"],
                    kind=capture.get("identity_kind", "unknown"),
                    lifecycle=capture.get("lifecycle", "unknown"),
                    source_as_of=capture.get("source_as_of"))
                result = evidence_review.append_private_receipt(args.private_output_dir, receipt)
                appended += result["status"] == "appended"
            report["private_evidence_receipts_appended"] = appended
    # Names, LEIs, websites, and candidate IDs are never written to public logs.
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
