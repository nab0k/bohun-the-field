"""R8 fail-closed checkpoint proposal. Pure logic, NEVER touches PostgreSQL.

Checkpoint advancement is not authorized by this module. It only validates a
hypothetical chain of committed, complete run receipts; write policy is separate.
"""
from datetime import datetime, timezone, timedelta

class CoverageError(ValueError):
    pass


def proposed_checkpoint(previous: datetime, receipts: list[dict], *,
                        max_overlap_seconds: int = 30) -> datetime:
    if not isinstance(previous, datetime) or previous.tzinfo is None:
        raise CoverageError("checkpoint_not_aware")
    if not isinstance(receipts, list) or not receipts:
        raise CoverageError("no_receipts")
    current = previous.astimezone(timezone.utc)
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise CoverageError("bad_receipt")
        a, b = receipt.get("window_start"), receipt.get("window_end")
        if (not isinstance(a, datetime) or a.tzinfo is None or
                not isinstance(b, datetime) or b.tzinfo is None):
            raise CoverageError("receipt_window_invalid")
        a, b = a.astimezone(timezone.utc), b.astimezone(timezone.utc)
        if (receipt.get("status") != "COMPLETE" or
                receipt.get("source_window_complete") is not True or
                receipt.get("transaction_committed") is not True or a >= b):
            raise CoverageError("receipt_unproven")
        if a > current:
            raise CoverageError("coverage_gap")
        if current - a > timedelta(seconds=max_overlap_seconds):
            raise CoverageError("overlap_too_large_or_receipt_out_of_order")
        if b <= current:
            raise CoverageError("no_checkpoint_progress")
        current = b
    return current  # Merely a proposal, not a database write.
