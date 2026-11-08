"""Offline scoring for C3-10 provisional targets; never an auto-merge gate."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
from .core import EditorPolicy, EditorVerdict


@dataclass(frozen=True)
class GoldCase:
    case_id: str
    category: str  # "deterministic" | "semantic" | "good"
    expected_rule_id: str
    verdict: EditorVerdict


@dataclass(frozen=True)
class Rate:
    name: str
    numerator: int
    denominator: int
    target: float
    direction: str  # "min" or "max"

    @property
    def value(self) -> float | None:
        return self.numerator / self.denominator if self.denominator else None

    @property
    def meets_point_target(self) -> bool | None:
        # Point estimates are engineering indicators, not statistical proof.
        if self.value is None:
            return None
        return self.value >= self.target if self.direction == "min" else self.value <= self.target


def _has_correct_reason(v: EditorVerdict, expected: str) -> bool:
    return any(r.rule_id == expected for r in v.reasons)


def score(cases: Iterable[GoldCase], policy: EditorPolicy | None = None) -> dict[str, Rate]:
    """Group by the gold label, not the verdict. REVIEW is NOT a PASS.

    For deterministic errors, only BLOCK with the correct rule counts.
    For semantic errors, BLOCK or REVIEW with the correct rule counts.
    Results report sample rates, not confidence bounds or production quality.
    """
    p = policy or EditorPolicy()
    data = list(cases)
    if len({c.case_id for c in data}) != len(data):
        raise ValueError("duplicate gold case IDs")
    if any(c.category not in {"deterministic", "semantic", "good"} for c in data):
        raise ValueError("unsupported gold category")
    det = [c for c in data if c.category == "deterministic"]
    sem = [c for c in data if c.category == "semantic"]
    good = [c for c in data if c.category == "good"]
    return {
        "deterministic_recall": Rate(
            "deterministic_recall",
            sum(c.verdict.decision == "BLOCK" and _has_correct_reason(
                c.verdict, c.expected_rule_id) for c in det), len(det),
            p.deterministic_recall_target, "min"),
        "semantic_recall": Rate(
            "semantic_recall",
            sum(c.verdict.decision in {"BLOCK", "REVIEW"} and _has_correct_reason(
                c.verdict, c.expected_rule_id) for c in sem), len(sem),
            p.semantic_recall_target, "min"),
        "false_block": Rate(
            "false_block",
            sum(c.verdict.decision == "BLOCK" for c in good), len(good),
            p.false_block_max, "max"),
        "good_review": Rate(
            "good_review",
            sum(c.verdict.decision == "REVIEW" for c in good), len(good),
            p.good_review_max, "max"),
    }
