"""Single bounded processing tick, suitable for a future scheduled cloud worker."""

from datetime import datetime, timedelta, timezone
import json

from . import PIPELINE_VERSION
from .contracts import (Document, JobSpec, StageResult, aware, exact_fields,
                        Usage, json_text, nonempty, string_list, validate_claims, validate_draft, validate_dossier)
from .ports import LeaseLost, QuotaExceeded


class Worker:
    def __init__(self, store, model, quota, *, clock=None, worker_id="worker",
                 lease_seconds=120, max_attempts=3, quota_is_final=False):
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("Positive attempt limit required")
        self.store, self.model, self.quota = store, model, quota
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.worker_id, self.lease_seconds, self.max_attempts = worker_id, lease_seconds, max_attempts
        self.halted_reason = None
        self.quota_is_final = quota_is_final

    def submit(self, document):
        return self.store.submit(document, PIPELINE_VERSION)

    def _finish(self, lease, status, payload, reasons=(), next_stage=None):
        successor = None
        if next_stage:
            successor = JobSpec(lease.spec.document_key, next_stage, json_text(payload), lease.spec.pipeline_version)
        result = StageResult(status, json_text(payload), tuple(reasons))
        self.store.finish(lease, self.clock(), result, successor)
        return status

    def tick(self):
        if self.halted_reason:
            return "STOPPED"
        lease = self.store.claim(self.worker_id, self.clock(), self.lease_seconds)
        if lease is None:
            return "IDLE"
        try:
            return self._process(lease)
        except LeaseLost:
            # A late response still incurs usage, but cannot commit after losing its lease.
            return "STALE"

    def _process(self, lease):
        if lease.attempt > self.max_attempts:
            return self._finish(lease, "HOLD", {}, ("RETRY_EXHAUSTED",))
        payload = json.loads(lease.spec.input_json)
        doc_data = dict(payload["document"])
        doc_data["verified_entity_ids"] = tuple(doc_data.get("verified_entity_ids", []))
        doc = Document(**doc_data)
        if not doc.complete or not doc.rights_verified or doc.sensitive:
            reasons = tuple(code for flag, code in (
                (not doc.complete, "INCOMPLETE_SOURCE"), (not doc.rights_verified, "RIGHTS_NOT_VERIFIED"),
                (doc.sensitive, "SENSITIVE_SOURCE")) if flag)
            return self._finish(lease, "HOLD", {}, reasons)
        # The adapter must estimate the entire request, not just the document text.
        try:
            estimate = self.model.estimate(lease.spec.stage, payload)
            if not isinstance(estimate, Usage) or min(estimate.input_tokens, estimate.output_tokens, estimate.compute_units) <= 0:
                raise ValueError("Nonzero complete reservation required")
        except Exception as error:
            from .gemini import ModelAccessStop
            if isinstance(error, ModelAccessStop):
                self.halted_reason = "MODEL_FREE_ACCESS_STOP"
            return self._finish(lease, "HOLD", {}, ("ESTIMATE_UNAVAILABLE",))
        request_id = lease.spec.job_id + ":" + lease.token
        try:
            self.quota.reserve(request_id, self.clock(), estimate)
        except QuotaExceeded:
            if self.quota_is_final:
                self.halted_reason = "RUN_QUOTA_BLOCKED"
                return self._finish(lease, "HOLD", {}, ("RUN_QUOTA_BLOCKED",))
            now = aware(self.clock())
            tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            self.store.defer(lease, now, tomorrow, "DAILY_QUOTA_BLOCKED")
            return "DEFERRED_QUOTA"
        self.quota.dispatch(request_id)
        try:
            reply = self.model.execute(lease.spec.stage, payload, request_id, estimate)
        except Exception as error:
            # Without an acknowledged provider usage result even a 5xx may be billable.
            self.quota.unknown(request_id)
            from .gemini import ModelAccessStop
            if isinstance(error, ModelAccessStop):
                self.halted_reason = "MODEL_FREE_ACCESS_STOP"
                return self._finish(lease, "HOLD", {}, ("MODEL_FREE_ACCESS_STOP",))
            if lease.attempt >= self.max_attempts:
                return self._finish(lease, "HOLD", {}, ("MODEL_CALL_UNCERTAIN", "RETRY_EXHAUSTED"))
            now = aware(self.clock())
            self.store.defer(lease, now, now + timedelta(seconds=min(60 * 2 ** (lease.attempt - 1), 900)),
                             "MODEL_CALL_UNCERTAIN")
            return "DEFERRED_MODEL"
        try:
            self.quota.settle(request_id, reply.usage)
        except QuotaExceeded:
            return self._finish(lease, "HOLD", {}, ("USAGE_EXCEEDS_RESERVATION",))
        except (ValueError, TypeError, AttributeError):
            self.quota.unknown(request_id)
            return self._finish(lease, "HOLD", {}, ("USAGE_UNAVAILABLE",))
        try:
            if len(json_text(reply.payload).encode("utf-8")) > 64 * 1024:
                raise ValueError("Output too large")
            return self._interpret(lease, doc, payload, reply.payload)
        except (ValueError, TypeError, KeyError):
            # Never copy provider errors/raw response text into operational logs.
            return self._finish(lease, "HOLD", {}, ("INVALID_MODEL_OUTPUT",))

    def _interpret(self, lease, doc, payload, output):
        if lease.spec.stage == "extract":
            reasons = validate_claims(doc, output)
            if reasons:
                return self._finish(lease, "HOLD", {}, reasons)
            next_payload = {**payload, "extraction": output,
                            "context_manifest": payload.get("context_manifest", []),
                            "context_status": payload.get("context_status", "NOT_RETRIEVED")}
            return self._finish(lease, "DONE", next_payload, next_stage="analyze")
        if lease.spec.stage == "analyze":
            dossier = None
            draft = output
            if payload.get("requires_dossier") is True:
                exact_fields(output, {"draft", "dossier"})
                draft, dossier = output["draft"], output["dossier"]
                validate_dossier(dossier, payload["extraction"]["claims"], payload["context_manifest"])
            validate_draft(draft, payload["extraction"]["claims"])
            return self._finish(lease, "DONE", {**payload, "draft": draft, "dossier": dossier}, next_stage="review")
        exact_fields(output, {"draft_hash", "issues", "comment"})
        from .contracts import digest
        review_subject = {"draft": payload["draft"], "dossier": payload["dossier"]} if payload.get("requires_dossier") else payload["draft"]
        if output["draft_hash"] != digest(review_subject):
            return self._finish(lease, "HOLD", {}, ("REVIEW_HASH_MISMATCH",))
        issues = string_list(output["issues"])
        nonempty(output["comment"])
        if issues:
            return self._finish(lease, "HOLD", {}, ("MODEL_REVIEW_ISSUES",))
        # This is a proposal for the editor, never an independent QA approval.
        packet = {"material_id": digest([doc.identity, lease.spec.pipeline_version]),
                  "origin": "machine", "editorial_status": "PROPOSED",
                  "draft": payload["draft"], "claims": payload["extraction"]["claims"],
                  "sources": [{"item_id": doc.item_id, "url": doc.url, "text_hash": doc.text_hash,
                               "published_at": doc.published_at, "fetched_at": doc.fetched_at}],
                  "context_manifest": payload["context_manifest"],
                  "context_status": payload["context_status"],
                  "dossier": payload.get("dossier"),
                  "coverage": payload.get("coverage", {}),
                  "analysis_run": {"pipeline_version":lease.spec.pipeline_version,
                                   "model":getattr(self.model,"model","supplied-replay"),
                                   "prompt_version":getattr(self.model,"prompt_version","supplied-replay")},
                  "warnings": (["CONTEXT_NOT_RETRIEVED"] if payload["context_status"] == "NOT_RETRIEVED" else []) +
                              ["SEMANTIC_QA_PENDING", "ENTITY_QA_NOT_PERFORMED"],
                  "reason_for_selection": "Passed structural/evidence checks; editorial usefulness unmeasured",
                  "qa": {"decision": "HOLD", "public_release": False}}
        return self._finish(lease, "EDITORIAL_READY", packet)
