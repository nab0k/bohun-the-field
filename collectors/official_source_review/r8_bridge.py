"""EXP-009 R8 bridge: complete read-only UK OCDS window -> R6 HOLD manifest.

This module does not create credentials, connect to databases or schedule jobs.
It deliberately rejects windows requiring a larger or different persistence
contract than the existing R7.1 pilot (1..10 tender-tagged releases, <=100 rows).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from r8_source_reader import SourceWindow, WindowError, read_window


class BridgeError(ValueError):
    pass


def prepare_complete_manifest(read_result: dict, prepare_releases: Callable) -> dict:
    """Convert a fully read, bounded source response to the legacy R6 pilot shape.

    `prepare_releases` is R6's pure `uk_hold_pilot.prepare_releases` function.
    A new persistence contract is needed for empty and >10-tender windows.
    """
    if not isinstance(read_result, dict) or read_result.get("complete") is not True:
        raise BridgeError("source_read_incomplete")
    window = read_result.get("window")
    if not isinstance(window, SourceWindow):
        raise BridgeError("source_window_missing")
    rows = read_result.get("releases")
    count = read_result.get("unique_releases")
    pages = read_result.get("pages")
    if (not isinstance(rows, list) or type(count) is not int or count != len(rows)
            or count > 100 or type(pages) is not int or pages < 1):
        raise BridgeError("r6_pilot_scope_exceeded")
    # R6 intentionally only works on <=100 releases from a fully known window.
    manifest = prepare_releases([{"releases": rows, "links": {}}], verified_complete=True)
    if not isinstance(manifest, dict):
        raise BridgeError("bad_r6_manifest")
    if manifest.get("observations") != count or manifest.get("source_window_complete") is not True:
        raise BridgeError("r6_observation_mismatch")
    tender_count = manifest.get("eligible_unique_tender_notices")
    if type(tender_count) is not int or not 1 <= tender_count <= 10:
        raise BridgeError("pilot_requires_1_to_10_tenders")
    if manifest.get("selection_limited") or manifest.get("pilot_candidates") != tender_count:
        raise BridgeError("pilot_truncation_forbidden")
    # This receipt is metadata from an in-memory *completed* reader. Not a
    # cryptographically authenticated external proof; the caller is trusted.
    return {
        **manifest,
        "r8_read_receipt": {
            "window": window,
            "complete": True,
            "pages": pages,
            "unique_releases": count,
            "duplicate_releases": read_result.get("duplicate_releases"),
        },
    }


def require_bound_receipt(manifest: dict, *, window_start: datetime, window_end: datetime) -> None:
    """Ensure persisted receipt UTC bounds equal the *actual request* bounds.

    Publication dates are deliberately NOT compared with last-updated windows.
    This replaces the broken published_at-window heuristic in R7.1.
    """
    if not isinstance(manifest, dict):
        raise BridgeError("manifest_missing")
    receipt = manifest.get("r8_read_receipt")
    if not isinstance(receipt, dict) or receipt.get("complete") is not True:
        raise BridgeError("complete_source_read_receipt_required")
    window = receipt.get("window")
    if not isinstance(window, SourceWindow):
        raise BridgeError("read_window_missing")
    if (not isinstance(window_start, datetime) or window_start.tzinfo is None or
            not isinstance(window_end, datetime) or window_end.tzinfo is None):
        raise BridgeError("utc_bounds_required")
    a, b = window_start.astimezone(timezone.utc), window_end.astimezone(timezone.utc)
    if a != window.utc_from or b != window.utc_to:
        raise BridgeError("read_receipt_window_mismatch")
    if (type(receipt.get("pages")) is not int or receipt["pages"] < 1 or
            type(receipt.get("unique_releases")) is not int or
            receipt["unique_releases"] != manifest.get("observations") or
            type(receipt.get("duplicate_releases")) is not int or
            receipt["duplicate_releases"] < 0):
        raise BridgeError("read_receipt_counts_invalid")


def prepare_window(window: SourceWindow, transport: Callable[[str], bytes], prepare_releases: Callable, **read_limits) -> dict:
    """Network only via explicit transport; returns no database side effects."""
    return prepare_complete_manifest(read_window(window, transport, **read_limits), prepare_releases)
