"""Offline reference adapters ONLY. No network, credentials, or durable cloud state."""

from copy import deepcopy
from datetime import timedelta
import json
from threading import RLock
from uuid import uuid4

from .contracts import JobSpec, Lease, ModelReply, Usage, STAGES, aware, json_text
from .ports import LeaseLost


class MemoryStore:
    """Thread-safe reference for the cloud-store contract, not restart persistence."""

    def __init__(self):
        self._lock = RLock()
        self._jobs = {}
        self._receipts = {}

    def _enqueue(self, spec):
        self._jobs.setdefault(spec.job_id, {"spec": spec, "state": "QUEUED", "attempts": 0,
                                          "token": None, "expires": None, "due": None,
                                          "result": None, "reason": None})
        return spec.job_id

    def submit(self, document, pipeline_version):
        with self._lock:
            receipts = self._receipts.setdefault(document.identity, [])
            if document.to_dict() not in receipts:
                receipts.append(deepcopy(document.to_dict()))
            # First receipt owns the original source reference; retain later aliases.
            payload = {"document": receipts[0]}
            return self._enqueue(JobSpec(document.identity, "extract", json_text(payload), pipeline_version))

    def receipts(self, document_key):
        with self._lock:
            return deepcopy(self._receipts[document_key])

    def claim(self, worker, now, lease_seconds=120):
        now = aware(now)
        if not worker or type(lease_seconds) is not int or lease_seconds <= 0:
            raise ValueError("Worker and positive lease duration required")
        with self._lock:
            for row in self._jobs.values():
                due = row["due"] is None or row["due"] <= now
                expired = row["state"] == "LEASED" and row["expires"] <= now
                if due and (row["state"] == "QUEUED" or expired):
                    row["attempts"] += 1
                    row.update(state="LEASED", token=worker + ":" + uuid4().hex,
                               expires=now + timedelta(seconds=lease_seconds))
                    return Lease(row["spec"], row["token"], row["attempts"], row["expires"])
        return None

    def _owned(self, lease, now):
        row = self._jobs[lease.spec.job_id]
        if row["state"] != "LEASED" or row["token"] != lease.token or row["expires"] <= aware(now):
            raise LeaseLost("STALE_LEASE")
        return row

    def finish(self, lease, now, result, successor=None):
        with self._lock:
            row = self._owned(lease, now)
            if successor is not None:
                if successor.document_key != lease.spec.document_key or successor.pipeline_version != lease.spec.pipeline_version:
                    raise ValueError("Successor changed document/pipeline identity")
                index = STAGES.index(lease.spec.stage)
                if result.status != "DONE" or index + 1 >= len(STAGES) or successor.stage != STAGES[index + 1]:
                    raise ValueError("Invalid stage transition")
                self._enqueue(successor)
            row.update(state="FINISHED", result=result, reason=None)

    def defer(self, lease, now, not_before, reason):
        if aware(not_before) <= aware(now):
            raise ValueError("Retry must be in the future")
        with self._lock:
            row = self._owned(lease, now)
            if reason == "DAILY_QUOTA_BLOCKED":
                # Waiting for a budget window must not exhaust model retries.
                row["attempts"] -= 1
            row.update(state="QUEUED", token=None, due=aware(not_before), reason=reason)

    def snapshot(self):
        with self._lock:
            return [{"job_id": k, "stage": r["spec"].stage, "state": r["state"],
                     "attempts": r["attempts"], "reason": r["reason"],
                     "result": json.loads(r["result"].output_json) if r["result"] else None,
                     "result_status": r["result"].status if r["result"] else None,
                     "reasons": list(r["result"].reasons) if r["result"] else []}
                    for k, r in self._jobs.items()]


class ReplayModel:
    """Returns explicitly supplied model responses; NEVER generates real analysis.

    Keys are '<document identity>:<stage>'. One copy per test worker; the store
    and quota reference adapters may be shared by concurrent workers.
    """

    def __init__(self, records):
        self.records = deepcopy(records)
        self.calls = []

    def _record(self, stage, payload):
        doc = payload["document"]
        from .contracts import Document
        doc = {**doc, "verified_entity_ids": tuple(doc.get("verified_entity_ids", []))}
        return self.records[Document(**doc).identity + ":" + stage]

    def estimate(self, stage, payload):
        return Usage(**self._record(stage, payload)["estimate"])

    def execute(self, stage, payload, request_id, maximum):
        record = self._record(stage, payload)
        self.calls.append({"request_id": request_id, "stage": stage})
        if record.get("timeout"):
            raise TimeoutError("Synthetic replay timeout")
        return ModelReply(deepcopy(record["payload"]), Usage(**record["usage"]))
