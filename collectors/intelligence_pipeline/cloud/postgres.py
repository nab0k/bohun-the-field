"""Persistent Store/Quota/outbox ports through one private, transactional RPC.

Requires the separately reviewed INPUT27 SQL candidate; never installs it.
"""

from datetime import datetime
import json
from ..contracts import Document, JobSpec, Lease, Usage, aware, digest, json_text
from ..ports import LeaseLost, QuotaExceeded
from . import CONTRACT, MODEL, TRIAL_MICRO_USD, cost
from .http import CloudError


class PostgresRPC:
    def __init__(self, connect):
        self.connect = connect

    def __call__(self, action, payload):
        connection = None
        try:
            connection = self.connect()
            if connection.autocommit is not False:
                raise CloudError("DB_AUTOCOMMIT_FORBIDDEN")
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = '15s'")
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("SELECT current_user, session_user")
                if cursor.fetchone() != ("bohun_analysis", "bohun_analysis"):
                    raise CloudError("DB_ROLE_INVALID")
                cursor.execute("SELECT exp009.cloud_rpc_v2(%s, %s::jsonb)",
                               (action, json_text(payload)))
                row = cursor.fetchone()
                if row is None:
                    raise CloudError("DB_RESULT_INVALID")
                value = row[0]
            # A lost COMMIT response must never trigger automatic replay.
            connection.commit()
            if isinstance(value, str):
                value = json.loads(value)
            return value
        except Exception as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
            code = getattr(exc, "sqlstate", None)
            if code == "P2701":
                raise QuotaExceeded("TRIAL_BUDGET_BLOCKED") from None
            if code == "P2702":
                raise LeaseLost("LEASE_LOST") from None
            raise CloudError("DB_RESULT_UNCERTAIN") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def open_rpc(dsn):
    # No import, driver setup or connection at module import time.
    try:
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        info = conninfo_to_dict(dsn)
    except Exception:
        raise CloudError("DB_DRIVER_OR_DSN_INVALID") from None
    if info.get("user") != "bohun_analysis" or info.get("sslmode") != "verify-full":
        raise CloudError("DB_LIMITED_ROLE_TLS_REQUIRED")
    return PostgresRPC(lambda: psycopg.connect(dsn, connect_timeout=10, autocommit=False))


class PostgresStore:
    def __init__(self, rpc, scope):
        self.rpc, self.scope = rpc, scope

    def submit(self, document, pipeline_version, *, evidence_receipt=None, context=None, category="change"):
        if not isinstance(document, Document):
            raise ValueError("DOCUMENT_REQUIRED")
        context = context or {}
        payload = {"document": document.to_dict(), "requires_dossier": True, "research_category":category,
                   "context_manifest":context.get("context_manifest", []),
                   "context_status":context.get("context_status", "NOT_RETRIEVED"),
                   "coverage":context.get("coverage", {})}
        spec = JobSpec(document.identity, "extract", json_text(payload), pipeline_version)
        return self.rpc("submit", {"scope": self.scope, "document_key": document.identity,
            "document_json": json_text(document.to_dict()), "job_id": spec.job_id,
            "input_json": spec.input_json, "pipeline_version": pipeline_version,
            "evidence_receipt": evidence_receipt})["job_id"]

    def receive(self, value, raw_html, admission):
        from ..source_adapters import restore_source
        source = restore_source(value)
        payload = {k:v for k,v in value.items() if k != "receipt"}
        return self.rpc("receive", {"scope":self.scope, "source_key":source.identity,
            "payload":json_text(payload), "source_core_json":json_text(value["source"]),
            "receipt":value["receipt"], "raw_html":raw_html,
            "admission":admission})

    def profile(self, reference):
        from ..source_adapters import source_profile
        return source_profile(self.rpc("profile", {"scope":self.scope, "reference":reference}))

    def context(self, document, *, as_of, limit):
        import re
        return self.rpc("context", {"scope":self.scope, "terms":re.findall(r"[\w-]{3,40}",document.title.lower())[:8],
            "as_of":aware(as_of).isoformat(), "exclude_url":document.url, "limit":limit})

    def claim(self, worker, now, lease_seconds):
        aware(now)
        if not worker or type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("LEASE_CONFIG_INVALID")
        result = self.rpc("claim", {"scope": self.scope, "worker": worker, "seconds": lease_seconds})
        if result is None:
            return None
        spec = JobSpec(result["document_key"], result["stage"], result["input_json"], result["pipeline_version"])
        if spec.job_id != result["job_id"]:
            raise CloudError("DB_JOB_HASH_MISMATCH")
        return Lease(spec, result["token"], result["attempt"],
                     datetime.fromisoformat(result["expires_at"].replace("Z", "+00:00")))

    @staticmethod
    def _lease(lease):
        return {"job_id": lease.spec.job_id, "token": lease.token}

    def finish(self, lease, now, result, successor=None):
        aware(now)
        value = {**self._lease(lease), "status": result.status, "output_json": result.output_json,
                 "reasons": list(result.reasons), "successor": None, "outbox_key": None}
        if successor is not None:
            if (successor.document_key, successor.pipeline_version) != (lease.spec.document_key, lease.spec.pipeline_version):
                raise ValueError("SUCCESSOR_IDENTITY_CHANGED")
            value["successor"] = {"job_id": successor.job_id, "stage": successor.stage,
                                   "input_json": successor.input_json}
        if result.status == "EDITORIAL_READY":
            packet = json.loads(result.output_json)
            if packet["qa"] != {"decision": "HOLD", "public_release": False} or packet["editorial_status"] != "PROPOSED":
                raise ValueError("PRIVATE_EDITORIAL_PACKET_REQUIRED")
            value["outbox_key"] = digest(packet)
        self.rpc("finish", value)

    def defer(self, lease, now, not_before, reason):
        aware(now)
        self.rpc("defer", {**self._lease(lease), "due": aware(not_before).isoformat(), "reason": reason})


class PostgresQuota:
    def __init__(self, rpc, scope):
        if not isinstance(scope, str) or not scope or len(scope) > 120:
            raise ValueError("EXPLICIT_BUDGET_SCOPE_REQUIRED")
        self.rpc, self.scope = rpc, scope

    def verify(self, approval=None):
        result = self.rpc("budget", {"scope": self.scope})
        if result["contract"] != CONTRACT or result["model"] != MODEL or result["cap_micro_usd"] != TRIAL_MICRO_USD:
            raise CloudError("BUDGET_CONFIGURATION_MISMATCH")
        if approval is not None and (result.get("billing_mode") != "FREE_ONLY"
                or result.get("free_approval_ref") != approval.reference
                or result.get("key_sha256") != approval.key_sha256 or approval.account_scope != self.scope
                or aware(datetime.fromisoformat(result.get("free_expires_at", "").replace("Z", "+00:00"))) !=
                   aware(datetime.fromisoformat(approval.expires_at.replace("Z", "+00:00")))):
            raise CloudError("PERSISTED_FREE_APPROVAL_MISMATCH")
        return result

    def reserve(self, request_id, now, estimate):
        aware(now)
        if not isinstance(estimate, Usage):
            raise ValueError("USAGE_RESERVATION_REQUIRED")
        self.rpc("reserve", {"scope": self.scope, "request_id": request_id,
                             "reserved": cost(estimate), "estimate": estimate.__dict__})

    def dispatch(self, request_id):
        self.rpc("dispatch", {"scope": self.scope, "request_id": request_id})

    def unknown(self, request_id):
        self.rpc("unknown", {"scope": self.scope, "request_id": request_id})

    def settle(self, request_id, actual):
        if not isinstance(actual, Usage):
            raise ValueError("MEASURED_USAGE_REQUIRED")
        result = self.rpc("settle", {"scope": self.scope, "request_id": request_id,
                                    "actual": cost(actual), "usage": actual.__dict__})
        if result["over"]:
            # The RPC COMMIT persisted the freeze before the exception escapes.
            raise QuotaExceeded("USAGE_EXCEEDS_RESERVATION")


class PostgresOutbox:
    def __init__(self, rpc, scope):
        self.rpc, self.scope = rpc, scope

    def claim(self):
        return self.rpc("delivery_claim", {"scope": self.scope})

    def finish(self, row, status, page_id=None):
        if status not in {"DELIVERED", "UNKNOWN", "CONFLICT", "HOLD"}:
            raise ValueError("DELIVERY_STATUS_INVALID")
        return self.rpc("delivery_finish", {"key": row["key"], "token": row["token"],
                      "status": status, "page_id": page_id})

    def uncertain(self, key):
        return self.rpc("delivery_read", {"scope": self.scope, "key": key})

    def reconcile(self, key, status, page_id):
        if status not in {"DELIVERED", "CONFLICT"}:
            raise ValueError("RECONCILIATION_STATUS_INVALID")
        return self.rpc("delivery_reconciled", {"scope": self.scope, "key": key,
                         "status": status, "page_id": page_id})
