"""BOHUN EXP-009 R9: validate a complete UK source window for atomic staging.

Pure local planning; never sends HTTP, opens PostgreSQL or advances a checkpoint.
For R6 normalization, partition complete R8 source results into <=10-release
fragments. This is NOT truncation: all fragments must pass the legacy HOLD gate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from r8_source_reader import SourceWindow, make_window, WindowError


class BatchPlanError(ValueError):
    """Fail closed; the source window must not be marked complete."""


@dataclass(frozen=True)
class WindowBatch:
    window: SourceWindow
    pages: int
    source_unique_releases: int
    source_duplicate_releases: int
    candidates: tuple
    qa: str = "HOLD"
    public_release: bool = False

    @property
    def observed_releases(self) -> int:
        return self.source_unique_releases + self.source_duplicate_releases

    @property
    def tender_candidates(self) -> int:
        return len(self.candidates)


def prepare_batch(read_result: dict, prepare_releases: Callable, *,
                  max_releases: int = 2500, max_tenders: int = 200) -> WindowBatch:
    """Normalize *every* release in a complete paginated source snapshot.

    `prepare_releases` is the existing R6 uk_hold_pilot.prepare_releases.
    Abort if any slice is rejected or unexpectedly truncated. Empty windows
    and windows with no tender-tagged notices are valid complete observations.
    """
    if not isinstance(read_result, dict) or read_result.get("complete") is not True:
        raise BatchPlanError("source_incomplete")
    window = read_result.get("window")
    rows = read_result.get("releases")
    pages = read_result.get("pages")
    uniques = read_result.get("unique_releases")
    duplicate = read_result.get("duplicate_releases")
    if (not isinstance(window, SourceWindow) or not isinstance(rows, list)
            or type(pages) is not int or pages < 1
            or type(uniques) is not int or uniques != len(rows)
            or type(duplicate) is not int or duplicate < 0):
        raise BatchPlanError("source_receipt_malformed")
    # Dataclass identity by itself is not proof of a well-formed source window.
    try:
        expected_window = make_window(window.local_from, window.local_to) if isinstance(window, SourceWindow) else None
    except (WindowError, AttributeError):
        raise BatchPlanError("source_window_untrusted") from None
    if expected_window != window:
        raise BatchPlanError("source_window_untrusted")
    if (type(max_releases) is not int or type(max_tenders) is not int
            or not 1 <= max_releases <= 2500 or not 1 <= max_tenders <= 2500):
        raise BatchPlanError("batch_limits_invalid")
    if uniques > max_releases:
        raise BatchPlanError("source_window_over_cap")
    # R8 guarantees unique (ocid, release.id) across pages. Verify before
    # slicing: the R6 fragment normalizer deduplicates only inside each slice,
    # which would otherwise allow a false complete count.
    identities = []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("ocid"), str)
                or not isinstance(row.get("id"), str) or not row["ocid"] or not row["id"]):
            raise BatchPlanError("source_identity_invalid")
        identities.append((row["ocid"], row["id"]))
    if len(identities) != len(set(identities)):
        raise BatchPlanError("duplicate_source_identity")
    if not callable(prepare_releases):
        raise BatchPlanError("normalizer_missing")
    candidates = []
    for start in range(0, uniques, 10):
        slice_rows = rows[start:start + 10]
        manifest = prepare_releases([{"releases": slice_rows, "links": {}}],
                                    verified_complete=True, max_tenders=10)
        expected = manifest.get("eligible_unique_tender_notices")
        selected = manifest.get("candidates")
        if (manifest.get("source") != "uk_fts" or manifest.get("qa") != "HOLD"
                or manifest.get("public_release") is not False
                or manifest.get("source_window_complete") is not True
                or manifest.get("selection_limited") is not False
                or type(expected) is not int or expected < 0 or expected > 10
                or not isinstance(selected, list) or len(selected) != expected
                or manifest.get("pilot_candidates") != expected
                or manifest.get("observations") != len(slice_rows)):
            raise BatchPlanError("normalizer_fragment_invalid")
        candidates.extend(selected)
        if len(candidates) > max_tenders:
            raise BatchPlanError("tender_count_over_cap")
    if any(getattr(c, "item_type", None) != "tender" for c in candidates):
        raise BatchPlanError("normalizer_kind_invalid")
    identities = [getattr(c, "external_id", None) for c in candidates]
    if (any(not isinstance(x, str) or not x for x in identities)
            or len(identities) != len(set(identities))):
        raise BatchPlanError("tender_identity_collision")
    candidates.sort(key=lambda c: c.external_id)
    return WindowBatch(window=window, pages=pages,
                       source_unique_releases=uniques,
                       source_duplicate_releases=duplicate,
                       candidates=tuple(candidates))
