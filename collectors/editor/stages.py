"""C3-10: conservative offline structural checks for event-stage assertions.

A stage is not inferred from the previous stage. This module checks dates,
explicit source/span attribution and event matching, not semantic truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

STAGES = frozenset({"announced", "contracted", "delivered", "demonstrated"})


@dataclass(frozen=True)
class StageIssue:
    severity: str  # BLOCK | REVIEW
    code: str


def _date(value: Any) -> date:
    if not isinstance(value, str) or not value:
        raise ValueError("missing date")
    if len(value) == 10:
        return date.fromisoformat(value)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("timezone required")
    return dt.date()


def check_stage_assertions(
    payload: Mapping[str, Any],
    verified_spans: Mapping[tuple[str, str], str],
    event_ids: tuple[str, ...],
) -> tuple[StageIssue, ...]:
    """Validate optional stage_assertions without guessing an event lifecycle.

    The source/spans provided here must already be checked by the caller.
    This does not assert that the quoted text semantically proves the stage.
    """
    if "stage_assertions" not in payload:
        return ()
    assertions = payload["stage_assertions"]
    if not isinstance(assertions, (list, tuple)) or not assertions:
        return (StageIssue("BLOCK", "invalid_stage_collection"),)
    issues: list[StageIssue] = []
    try:
        as_of = _date(payload["as_of"]) if "as_of" in payload else None
    except (ValueError, OverflowError, TypeError):
        return (StageIssue("BLOCK", "invalid_stage_as_of"),)
    for entry in assertions:
        if not isinstance(entry, Mapping):
            issues.append(StageIssue("BLOCK", "invalid_stage_assertion"))
            continue
        stage = entry.get("stage")
        if not isinstance(stage, str) or stage not in STAGES:
            issues.append(StageIssue("BLOCK", "invalid_stage_value"))
        source_id, span_id = entry.get("source_id"), entry.get("span_id")
        if (not isinstance(source_id, str) or not isinstance(span_id, str)
                or not source_id or not span_id or (source_id, span_id) not in verified_spans):
            issues.append(StageIssue("BLOCK", "stage_without_source_span"))
        event_id = entry.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id not in event_ids:
            issues.append(StageIssue("BLOCK", "stage_event_mismatch"))
        try:
            when = _date(entry.get("event_date"))
        except (ValueError, OverflowError, TypeError):
            issues.append(StageIssue("BLOCK", "invalid_stage_event_date"))
            continue
        if as_of and when > as_of:
            issues.append(StageIssue("REVIEW", "stage_claim_postdates_snapshot"))
    return tuple(dict.fromkeys(issues))
