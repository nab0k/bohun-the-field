"""Narrow runtime API client. SQL draft must be separately reviewed/applied first."""
from dataclasses import asdict
from datetime import datetime
import json
import re

from .contracts import Document, JobSpec, Lease, Usage, aware, digest, json_text
from .ports import LeaseLost, QuotaExceeded, VersionConflict
from .source_adapters import restore_source
from .costing import MODEL, PRICE_VERSION, micro_usd


class DatabaseUnknown(RuntimeError):
    """A commit may have succeeded. Recover by ID, never rerun capture/dispatch."""


class RuntimeAPI:
    def __init__(self, factory):
        self.factory = factory

    def call(self, operation, arguments, *, readonly=False):
        conn = None; committing = False
        try:
            conn = self.factory()
            if conn.autocommit:
                raise ValueError("RUNTIME_AUTOCOMMIT_FORBIDDEN")
            if readonly:
                conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '30s'")
            conn.execute("SET LOCAL lock_timeout = '5s'")
            row = conn.execute("SELECT current_user, session_user, "
                               "(SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid())").fetchone()
            if row != ("bohun_runtime", "bohun_runtime", True):
                raise ValueError("RUNTIME_ROLE_OR_TLS_INVALID")
            result = conn.execute("SELECT exp009.runtime_api(%s::text, %s::jsonb)",
                                  (operation, json_text(arguments))).fetchone()[0]
            if not isinstance(result, dict) or "status" not in result:
                raise ValueError("RUNTIME_REPLY_INVALID")
            if readonly:
                conn.rollback()
            else:
                committing = True
                conn.commit()
            if result["status"] == "STALE":
                raise LeaseLost("STALE_LEASE")
            if result["status"] == "QUOTA_BLOCKED":
                raise QuotaExceeded("RUNTIME_QUOTA_BLOCKED")
            if result["status"] == "CONFLICT":
                raise VersionConflict("RUNTIME_VERSION_CONFLICT")
            if result["status"] != "OK":
                raise ValueError("RUNTIME_OPERATION_REFUSED")
            return result.get("value")
        except (LeaseLost, QuotaExceeded, VersionConflict):
            raise
        except Exception:
            if committing:
                raise DatabaseUnknown("DB_COMMIT_UNKNOWN_READ_ID_NO_RETRY") from None
            raise RuntimeError("RUNTIME_DB_FAILED_NO_RETRY") from None
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def connect_factory(dsn):
    """No connection at construction. Refuse unverified TLS before first connect."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    params = conninfo_to_dict(dsn)
    if params.get("sslmode") != "verify-full" or not params.get("sslrootcert"):
        raise ValueError("VERIFIED_TLS_DSN_REQUIRED")
    return lambda: psycopg.connect(dsn, connect_timeout=10, autocommit=False)


class PostgresStore:
    def __init__(self, api, run_scope=""):
        self.api, self.run_scope = api, run_scope

    def receive(self, value):
        source = restore_source(value)
        core = {k:v for k,v in value.items() if k != "receipt"}
        payload = json_text(core)
        # Persisting body provenance does not grant eligibility for analysis.
        return self.api.call("receive", {"payload":payload, "payload_hash":digest(core),
                                        "receipt":value["receipt"], "source_key":source.identity})

    def submit(self, document, pipeline_version):
        return self.submit_with_context(document, pipeline_version, {})

    def submit_with_context(self, document, pipeline_version, context, *, category="change"):
        if category not in {"change", "product", "contract", "policy"}:
            raise ValueError("RESEARCH_CATEGORY_INVALID")
        payload = {"document":document.to_dict(), "requires_dossier":True, "research_category":category,
                   "run_scope":self.run_scope,
                   "context_manifest":context.get("context_manifest", []),
                   "context_status":context.get("context_status", "NOT_RETRIEVED"),
                   "coverage":context.get("coverage", {})}
        spec = JobSpec(document.identity, "extract", json_text(payload), pipeline_version)
        return self.api.call("submit", {"document_key":document.identity, "spec":asdict(spec), "job_key":spec.job_id})

    def claim(self, worker, now, lease_seconds):
        if not worker or type(lease_seconds) is not int or not 1 <= lease_seconds <= 300:
            raise ValueError("LEASE_REQUEST_INVALID")
        value = self.api.call("claim", {"worker":worker, "lease_seconds":lease_seconds, "scope":self.run_scope})
        if value is None:
            return None
        return Lease(JobSpec(**value["spec"]), value["token"], value["attempt"],
                     aware(datetime.fromisoformat(value["expires_at"])))

    def finish(self, lease, now, result, successor=None):
        self.api.call("finish", {"job_key":lease.spec.job_id, "token":lease.token,
                               "result":asdict(result), "successor":asdict(successor) if successor else None,
                               "successor_key":successor.job_id if successor else None})

    def defer(self, lease, now, not_before, reason):
        if aware(not_before) <= aware(now):
            raise ValueError("DEFER_MUST_BE_FUTURE")
        self.api.call("defer", {"job_key":lease.spec.job_id, "token":lease.token,
                              "not_before":aware(not_before).isoformat(), "reason":reason})

    def context(self, document, *, as_of, limit):
        terms = re.findall(r"[\w-]{3,40}", document.title.lower())[:8]
        return self.api.call("context", {"terms":terms, "as_of":aware(as_of).isoformat(),
                                        "exclude_url":document.url, "limit":limit}, readonly=True)

    def ready(self, limit=5):
        if type(limit) is not int or not 1 <= limit <= 5:
            raise ValueError("READY_LIMIT_INVALID")
        return self.api.call("ready", {"limit":limit, "scope":self.run_scope}, readonly=True)

    def propose(self, packet):
        from .editorial import MaterialVersion
        if packet.get("qa") != {"decision":"HOLD", "public_release":False}:
            raise ValueError("MACHINE_CANNOT_SET_QA")
        head = self.api.call("material_head", {"material_id":packet["material_id"]}, readonly=True)
        previous = head["max_version"] if head else 0
        version = MaterialVersion(packet["material_id"], previous + 1, packet["draft"]["title"],
                                  packet["draft"]["body"], tuple(s["text_hash"] for s in packet["sources"]), "machine")
        return self.api.call("propose", {"packet":packet, "packet_json":json_text(packet), "packet_hash":digest(packet),
                                        "expected_version":previous, "material":asdict(version),
                                        "content_hash":version.content_hash})

    def delivery(self, material_id, version):
        return self.api.call("delivery_read", {"material_id":material_id, "version":version}, readonly=True)

    def claim_delivery(self, material_id, version, *, expected_hash, packet_hash):
        return self.api.call("delivery_claim", {"material_id":material_id, "version":version,
                                               "content_hash":expected_hash, "packet_hash":packet_hash})

    def mark_delivery(self, material_id, version, state, *, page_id=None, expected_hash, token=None):
        if state not in {"SENDING", "UNKNOWN", "CONFIRMED", "CONFLICT"}:
            raise ValueError("DELIVERY_STATE_INVALID")
        return self.api.call("delivery_mark", {"material_id":material_id, "version":version,
                                              "state":state, "page_id":page_id, "content_hash":expected_hash,
                                              "token":token})


class PostgresQuota:
    """One persisted run/account scope, never reset on process restart or midnight."""
    def __init__(self, api, scope, approval=None):
        if not isinstance(scope, str) or not scope.strip():
            raise ValueError("QUOTA_SCOPE_REQUIRED")
        self.api, self.scope, self.approval = api, scope, approval
        self._verified = False

    def verify(self):
        from .gemini import FreeApproval, PaidApproval
        if type(self.approval) not in {FreeApproval, PaidApproval} or self.approval.account_scope != self.scope:
            raise ValueError("SCOPE_APPROVAL_BINDING_REQUIRED")
        value = self.api.call("limits", {"scope":self.scope}, readonly=True)
        expected = {"contract_version":"exp009-runtime-input29-v3", "scope":self.scope, "access_mode":self.approval.mode,
                    "approval_ref":self.approval.reference, "key_sha256":self.approval.key_sha256,
                    "model":MODEL, "price_version":PRICE_VERSION,
                    "cap_micro_usd":getattr(self.approval, "cap_micro_usd", 0)}
        if not isinstance(value, dict) or any(value.get(k) != v for k,v in expected.items()):
            raise ValueError("PERSISTED_ACCESS_OR_BUDGET_MISMATCH")
        at = lambda s: aware(datetime.fromisoformat(s.replace("Z", "+00:00")))
        if at(value["expires_at"]) != at(self.approval.expires_at):
            raise ValueError("PERSISTED_APPROVAL_EXPIRY_MISMATCH")
        if value["enabled"] is not True or value["frozen"] is not False:
            raise QuotaExceeded("RUNTIME_SCOPE_DISABLED_OR_FROZEN")
        self._verified = True
        return value

    def reserve(self, request_id, now, estimate):
        if not self._verified:
            self.verify()
        self.api.call("reserve", {"scope":self.scope, "request_id":request_id, "estimate":asdict(estimate),
                                  "micro_usd":micro_usd(estimate)})

    def dispatch(self, request_id):
        self.api.call("dispatch", {"scope":self.scope, "request_id":request_id})

    def settle(self, request_id, actual):
        self.api.call("settle", {"scope":self.scope, "request_id":request_id, "actual":asdict(actual),
                                 "micro_usd":micro_usd(actual)})

    def unknown(self, request_id):
        self.api.call("unknown", {"scope":self.scope, "request_id":request_id})
