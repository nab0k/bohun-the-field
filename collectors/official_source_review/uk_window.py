"""EXP-009 stage 1: select tender + tenderUpdate releases of one verified-complete window.

In memory only, no database. Releases are deduplicated, checked for conflicting
versions and ordered by (release date, release id) as the writer applies them (D5).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
import json

import normalize
from r5_quality_hardened import _stable_identity
import uk_fields

MAX_WINDOW_RECORDS = 200   # uk_probe reads at most 2 pages of 100
MAX_STAGE1_RELEASES = 50   # temporary stage-1 guard; volume limits come in stage 3


class WindowError(ValueError):
    pass


@dataclass(frozen=True)
class Release:
    ocid: str
    release_id: str
    tag: str
    release_date: datetime
    sha256: str
    clean: dict
    fields: dict


def _package_meta(pages):
    meta = set()
    for page in pages:
        lic = page.get("license")
        publisher = (page.get("publisher") or {}).get("name") if isinstance(page.get("publisher"), dict) else None
        if not isinstance(lic, str) or not lic.startswith("http") or len(lic) > 300:
            raise WindowError("package_license_missing")
        if not isinstance(publisher, str) or not publisher.strip() or len(publisher) > 200:
            raise WindowError("package_publisher_missing")
        meta.add((lic, publisher.strip()))
    if len(meta) != 1:
        raise WindowError("package_meta_differs_between_pages")
    return meta.pop()


def _release(row):
    if _stable_identity("uk", row) is not None:
        raise WindowError("identity_invalid_or_overlong")
    if not isinstance(row.get("ocid"), str) or not isinstance(row.get("id"), str):
        raise WindowError("identity_not_string")
    try:
        tag = uk_fields.release_tag(row)
        if tag is None:
            return None
        normalize.uk_release(row)  # existing R5 contract: id and title present
        clean = uk_fields.sanitize(row)
        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > uk_fields.MAX_RELEASE_BYTES:
            raise WindowError("release_too_large")
        return Release(
            ocid=row["ocid"].strip(), release_id=row["id"].strip(), tag=tag,
            release_date=uk_fields.parse_time(row.get("date"), "bad_release_date"),
            sha256=uk_fields.release_hash(clean), clean=clean,
            fields=uk_fields.extract(row),
        )
    except (uk_fields.FieldError, normalize.NormalizationError) as exc:
        raise WindowError(str(exc)) from None


def prepare_window(pages, *, verified_complete, max_releases=MAX_STAGE1_RELEASES):
    if type(verified_complete) is not bool or not verified_complete:
        raise WindowError("source_window_not_verified_complete")
    if not isinstance(pages, list) or not pages:
        raise WindowError("source_pages_missing")
    rows = []
    for i, page in enumerate(pages):
        if not isinstance(page, dict) or not isinstance(page.get("releases"), list):
            raise WindowError("malformed_source_page")
        if i == len(pages) - 1 and (page.get("links") or {}).get("next"):
            raise WindowError("window_incomplete")
        rows.extend(page["releases"])
    if len(rows) > MAX_WINDOW_RECORDS:
        raise WindowError("source_window_too_large")
    license_url, publisher = _package_meta(pages)
    kinds = Counter()
    by_key = {}
    for row in rows:
        if not isinstance(row, dict):
            raise WindowError("malformed_source_record")
        r = _release(row)
        kinds[r.tag if r else "other"] += 1
        if r is None:
            continue
        key = (r.ocid, r.release_id)
        prior = by_key.get(key)
        if prior is not None and prior.sha256 != r.sha256:
            raise WindowError("same_release_conflicting_versions")
        by_key[key] = r
    if len(by_key) > max_releases:
        raise WindowError("stage1_release_limit_exceeded")
    releases = sorted(by_key.values(), key=lambda r: (r.release_date, r.release_id))
    return {
        "source": "uk_fts", "qa": "HOLD", "public_release": False,
        "source_window_complete": True, "observations": len(rows),
        "kinds": dict(sorted(kinds.items())), "releases": releases,
        "license": license_url, "publisher": publisher,
    }


def prepare_live_window(start, end, *, transport=None):
    """One bounded read through the existing guarded reader; pages kept in memory."""
    import official_sources
    responses = []
    fetch = transport if transport is not None else official_sources._get

    def capture(url, **kwargs):
        raw = fetch(url, **kwargs)
        if not isinstance(raw, bytes):
            raise WindowError("source_payload_not_bytes")
        responses.append(raw)
        return raw

    receipt = official_sources.uk_probe(start, end, max_pages=2, transport=capture)
    if receipt.get("complete") is not True or len(responses) != receipt.get("pages"):
        raise WindowError("window_not_complete")
    try:
        pages = [json.loads(raw) for raw in responses]
    except (ValueError, UnicodeDecodeError):
        raise WindowError("source_json_invalid") from None
    window = prepare_window(pages, verified_complete=True)
    if window["observations"] != receipt.get("records"):
        raise WindowError("receipt_record_count_mismatch")
    return window
