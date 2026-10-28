"""Shared-account UTC-day reservations; uncertain calls retain their whole budget."""

from dataclasses import dataclass
from threading import RLock

from .contracts import Usage, aware
from .ports import QuotaExceeded


@dataclass(frozen=True)
class Limits:
    requests: int
    usage: Usage

    def __post_init__(self):
        if type(self.requests) is not int or self.requests < 0:
            raise ValueError("Nonnegative request limit required")
        if not isinstance(self.usage, Usage):
            raise ValueError("Explicit usage limits required")


class MemoryQuota:
    """Offline reference adapter, NOT a durable multi-run production budget ledger.

    All workers sharing an account must share ONE ledger. A cloud adapter must
    transact reserve/dispatch/settle and persist reservations across process exits.
    """

    def __init__(self, scope, limits):
        if not scope:
            raise ValueError("Explicit account budget scope required")
        self.scope, self.limits = scope, limits
        self._lock = RLock()
        self._entries = {}
        self._frozen = set()

    def _totals(self, day):
        entries = [r for r in self._entries.values() if r["day"] == day]
        total = Usage(0, 0, 0)
        for row in entries:
            total = total.plus(row["charged"])
        return len(entries), total

    def reserve(self, request_id, now, estimate):
        day = aware(now).date().isoformat()
        if not request_id or not isinstance(estimate, Usage):
            raise ValueError("Request identity and estimate required")
        with self._lock:
            if request_id in self._entries:
                raise QuotaExceeded("REQUEST_ALREADY_RESERVED")
            calls, used = self._totals(day)
            if day in self._frozen or calls >= self.limits.requests or not used.plus(estimate).fits(self.limits.usage):
                raise QuotaExceeded("DAILY_QUOTA_BLOCKED")
            self._entries[request_id] = {"day": day, "estimate": estimate,
                                         "charged": estimate, "state": "RESERVED"}

    def dispatch(self, request_id):
        with self._lock:
            row = self._entries[request_id]
            if row["state"] != "RESERVED":
                raise ValueError("Request was already dispatched")
            row["state"] = "DISPATCHED"

    def unknown(self, request_id):
        with self._lock:
            row = self._entries[request_id]
            if row["state"] not in {"DISPATCHED", "UNKNOWN"}:
                raise ValueError("Only dispatched requests can have uncertain spend")
            row["state"] = "UNKNOWN"
            # No refund. Retry has a new request ID and a second reservation.

    def settle(self, request_id, actual):
        if not isinstance(actual, Usage):
            raise ValueError("Measured usage required")
        with self._lock:
            row = self._entries[request_id]
            if row["state"] != "DISPATCHED":
                raise ValueError("Cannot settle unknown/undispatched request without reconciliation")
            row["charged"] = actual
            row["state"] = "SETTLED"
            if not actual.fits(row["estimate"]):
                self._frozen.add(row["day"])
                # Record real consumption even when the provider broke the estimate.
                raise QuotaExceeded("ACTUAL_USAGE_EXCEEDS_RESERVATION")

    def snapshot(self, now):
        day = aware(now).date().isoformat()
        with self._lock:
            calls, used = self._totals(day)
            return {"scope": self.scope, "day": day, "requests": calls,
                    "input_tokens": used.input_tokens, "output_tokens": used.output_tokens,
                    "compute_units": used.compute_units, "frozen": day in self._frozen,
                    "uncertain_requests": sum(r["day"] == day and r["state"] == "UNKNOWN"
                                              for r in self._entries.values())}
