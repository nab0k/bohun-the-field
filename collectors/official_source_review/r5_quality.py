"""R5: bounded live-source HOLD diagnostics. No persistence or publication.

The existing source reader is the sole network layer. Provider response bodies
are kept only in memory and are never included in emitted diagnostics.
"""
import argparse
import csv
import io
import json
from collections import Counter

import normalize
import official_sources
import r5_quality_hardened


SOURCES = ("uk", "ca_new", "ca_open", "eu")


def _rows(source, captures):
    if source == "uk":
        for raw in captures:
            for row in json.loads(raw)["releases"]:
                yield row
    elif source in ("ca_new", "ca_open"):
        assert len(captures) == 1
        yield from csv.DictReader(io.StringIO(captures[0].decode("utf-8-sig")))
    else:
        assert len(captures) == 1
        yield from json.loads(captures[0])["results"]


def count_quality(source, rows, *, complete, reported_total=None):
    """Aggregate only. No raw records, identifying keys or example titles."""
    if source not in SOURCES:
        raise ValueError("unsupported_source")
    by_id = {}
    kinds = Counter()
    rejected = Counter()
    total = duplicate = conflicts = 0
    converter = (normalize.uk_release if source == "uk" else
                 (lambda r: normalize.canada_row(r, dataset=source)) if source.startswith("ca_") else
                 normalize.eu_result)
    for row in rows:
        total += 1
        try:
            item = converter(row)
            if (item["verification_status"] != "HOLD" or
                item["relevance_status"] != "UNVERIFIED" or
                item["public_release"] is not False):
                raise ValueError("unsafe_candidate_state")
            key = item["external_id"]
            h = item["source_record_sha256"]
            previous = by_id.get(key)
            if previous is None:
                by_id[key] = h
                kinds[item["item_type_candidate"]] += 1
            elif previous == h:
                duplicate += 1
            else:
                conflicts += 1
        except Exception:
            # Never echo exception messages or provider field values to logs.
            rejected["invalid_record"] += 1
    return {
        "source": source, "qa": "HOLD", "public_release": False,
        "complete": bool(complete), "sample_only": not bool(complete),
        "records_seen": total, "unique_candidates": len(by_id),
        "duplicate_observations": duplicate, "conflicting_versions": conflicts,
        "rejected": sum(rejected.values()), "rejected_reasons": dict(rejected),
        "candidate_kinds": dict(sorted(kinds.items())),
        "reported_total": reported_total if isinstance(reported_total, int) else None,
        "defence_relevance_verified": 0,
        "ready_for_import": False,
    }


def live_quality(source, *, transport=None):
    """One bounded remote probe, plus in-memory conversion; no body on disk."""
    if source not in SOURCES:
        raise ValueError("unsupported_source")
    captures = []
    fetch = transport or official_sources._get

    def capture(url, **kwargs):
        raw = fetch(url, **kwargs)
        captures.append(raw)
        return raw

    if source == "uk":
        # Fixed historical one-hour diagnostic window, not coverage monitoring.
        probe = official_sources.uk_probe(
            "2026-10-15T09:00:00", "2026-10-15T10:00:00", transport=capture)
    elif source in ("ca_new", "ca_open"):
        probe = official_sources.canada_probe(source, transport=capture)
    else:
        probe = official_sources.eu_probe("EDF", transport=capture)
    report = r5_quality_hardened.quality_report(
        source, _rows(source, captures),
        source_complete=probe["complete"],
        reported_total=probe.get("reported_total"))
    if report["records_seen"] != probe["records"]:
        raise ValueError("probe_count_mismatch")
    report["pages_checked"] = probe.get("pages", 1)
    report["source_response_count"] = len(captures)
    return report


def main():
    p = argparse.ArgumentParser(description="Manual read-only normalized-quality check")
    p.add_argument("--live-read-only", action="store_true", required=True)
    p.add_argument("--source", choices=SOURCES, required=True)
    args = p.parse_args()
    try:
        result = live_quality(args.source)
        print(json.dumps(result, sort_keys=True))
    except (official_sources.ProbeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        print(json.dumps({"source": args.source, "qa": "HOLD",
                          "public_release": False, "status": "STOPPED",
                          "reason": "diagnostic_failed"}))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
