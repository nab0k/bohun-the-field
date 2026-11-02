"""One RPC adapter for QueuePort and A26 Backend. Injected transport; no IO here."""
from datetime import timedelta
import re

from . import contracts as c
from . import checkpoint as wire
from .changes import ChangeSet
from .worker import (CommitReceipt, ExternalOutcomeUnknown, LeaseLost, UnknownCommit,
                     WorkContext, check_lease)


class BackendFault(Exception):
    """Transport maps trusted SQLSTATE/detail to this bounded code, never raw DB text."""
    def __init__(self, code):
        self.code = code if type(code) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code) else "REMOTE_FAILURE"
        super().__init__(self.code)


def trigger_receipt(company_id, origin_id, content_hash, sections=("EVENTS",)):
    c.identifier(company_id); c.identifier(origin_id)
    c.require(c.is_hash(content_hash) and type(sections) is tuple and sections
              and all(s in c.SECTIONS for s in sections), "TRIGGER_INVALID")
    sections = sorted(set(sections))
    return {"company_id": company_id, "origin_id": origin_id, "content_hash": content_hash,
            "sections": sections, "key": c.digest([company_id, origin_id, content_hash, sections])}


class PostgresBackend:
    def __init__(self, rpc, *, policy, clock, card_factory=None, triggers=()):
        c.require(callable(rpc) and callable(clock) and type(policy) is c.Policy, "BACKEND_CONFIG_INVALID")
        self.rpc, self.policy, self.clock, self.card_factory = rpc, policy, clock, card_factory
        self.snapshots = {}
        self.registry_pages = {}
        self.triggers = {}
        self.last_batch = None
        c.require(type(triggers) is tuple and len(triggers) <= 500, "TRIGGER_LIMIT")
        for t in triggers:
            wire.shape(t, {"company_id", "origin_id", "content_hash", "sections", "key"})
            expected = trigger_receipt(t["company_id"], t["origin_id"], t["content_hash"], tuple(t["sections"]))
            c.require(t == expected and t["key"] not in self.triggers, "TRIGGER_DUPLICATE_OR_INVALID")
            self.triggers[t["key"]] = expected

    def _call(self, action, payload, *, mutating=False):
        request = wire.bounded({"wire_version": wire.VERSION, "policy_hash": self.policy.fingerprint,
                                "args": payload})
        try:
            response = self.rpc(action, request)
        except BackendFault as exc:
            if exc.code in ("LEASE_LOST", "LEASE_EXPIRED"):
                raise LeaseLost() from None
            raise c.ContractError(exc.code) from None
        except Exception:
            if mutating:
                raise UnknownCommit() from None
            raise BackendFault("RPC_READ_UNAVAILABLE") from None
        try:
            response = wire.bounded(response)
            wire.shape(response, {"wire_version", "status", "data"})
            c.require(type(response["wire_version"]) is int and response["wire_version"] == wire.VERSION
                      and response["status"] == "OK", "RPC_ENVELOPE_INVALID")
            return response["data"]
        except (c.ContractError, TypeError, ValueError):
            if mutating:
                raise UnknownCommit() from None
            raise c.ContractError("RPC_ENVELOPE_INVALID") from None

    def _lease(self, lease, now=None):
        now = c.aware(self.clock() if now is None else now)
        check_lease(lease, token=lease.token, now=now, policy=self.policy)
        return {"job_key": lease.job.job_key, "token": lease.token, "now": now.isoformat()}

    def claim(self, policy_hash, now, lease_seconds):
        c.require(policy_hash == self.policy.fingerprint and lease_seconds == self.policy.lease_seconds,
                  "POLICY_CHANGED")
        result = self._call("claim", {"now": c.aware(now).isoformat(), "lease_seconds": lease_seconds}, mutating=True)
        if result is None:
            return None
        lease = wire.decode(result, c.Lease)
        response_time = c.aware(self.clock())
        check_lease(lease, token=lease.token, now=response_time, policy=self.policy)
        c.require(lease.expires_at <= response_time+timedelta(seconds=lease_seconds), "LEASE_LIMIT_INVALID")
        return lease

    def context(self, lease):
        ctx = wire.decode(self._call("context", self._lease(lease)), WorkContext)
        c.require(ctx.input_hash == lease.job.input_hash and ctx.view.company_id == lease.job.company_id,
                  "INPUT_VERSION_OR_SCOPE_CHANGED")
        return ctx

    def prepared_result(self, lease):
        result = self._call("read_prepared", self._lease(lease))
        if result is None:
            return None
        changes = wire.validate_changes(wire.decode(result, ChangeSet), self.policy, self.clock())
        self._scope(lease, changes)
        return changes

    def _scope(self, lease, changes):
        c.require(changes.company_id == lease.job.company_id and changes.section == lease.job.section,
                  "PREPARED_SCOPE_MISMATCH")

    def save_prepared(self, lease, changes, now):
        wire.validate_changes(changes, self.policy, now); self._scope(lease, changes)
        try:
            result = self._call("save_prepared", self._lease(lease, now) |
                                {"changes": wire.encode(changes), "result_hash": changes.fingerprint}, mutating=True)
            c.require(result == {"result_hash": changes.fingerprint}, "PREPARED_RECEIPT_INVALID")
        except (UnknownCommit, c.ContractError) as exc:
            if isinstance(exc, c.ContractError) and str(exc) != "PREPARED_RECEIPT_INVALID":
                raise
            try:
                saved = self.prepared_result(lease)
            except Exception:
                saved = None
            if saved is None or saved.fingerprint != changes.fingerprint:
                raise ExternalOutcomeUnknown() from None

    def commit_result(self, lease, changes, now):
        wire.validate_changes(changes, self.policy, now); self._scope(lease, changes)
        try:
            raw = self._call("commit_result", self._lease(lease, now) |
                             {"result_hash": changes.fingerprint}, mutating=True)
            receipt = wire.decode(raw, CommitReceipt)
            c.require(receipt.job_key == lease.job.job_key and receipt.result_hash == changes.fingerprint
                      and receipt.state == changes.completion_state, "COMMIT_RECEIPT_INVALID")
            return receipt
        except (c.ContractError, TypeError, ValueError) as exc:
            if isinstance(exc, c.ContractError) and str(exc) not in ("COMMIT_RECEIPT_INVALID", "WIRE_TYPE_INVALID",
                    "WIRE_SHAPE_INVALID", "WIRE_VALUE_INVALID", "WIRE_FIELD_TYPE_INVALID"):
                raise
            raise UnknownCommit() from None

    def lookup_result(self, job_key, result_hash):
        c.require(c.is_hash(job_key) and c.is_hash(result_hash), "RECEIPT_KEY_INVALID")
        raw = self._call("lookup_result", {"job_key": job_key, "result_hash": result_hash})
        if raw is None:
            return None
        receipt = wire.decode(raw, CommitReceipt)
        c.require(receipt.job_key == job_key and receipt.result_hash == result_hash, "RECEIPT_SCOPE_INVALID")
        return receipt

    def defer(self, lease, state, reason, not_before, now):
        c.require(state in ("WAIT_SOURCE", "WAIT_QUOTA", "NEEDS_REVIEW", "FAILED"), "DEFER_STATE_INVALID")
        c.require(type(reason) is str and bool(re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", reason)), "SAFE_REASON_REQUIRED")
        if state in ("WAIT_SOURCE", "WAIT_QUOTA"):
            c.require(not_before is not None and c.aware(not_before) > c.aware(now), "FUTURE_RETRY_REQUIRED")
        else:
            c.require(not_before is None, "DEFER_TIME_UNEXPECTED")
        result = self._call("defer", self._lease(lease, now) | {"state": state, "reason": reason,
                            "not_before": c.aware(not_before).isoformat() if not_before else None}, mutating=True)
        if result != {"state": state}:
            raise UnknownCommit()

    def renew(self, lease, now):
        renewed = wire.decode(self._call("renew", self._lease(lease, now), mutating=True), c.Lease)
        c.require(renewed.token == lease.token and renewed.job == lease.job, "LEASE_SCOPE_INVALID")
        check_lease(renewed, token=lease.token, now=now, policy=self.policy)
        c.require(renewed.expires_at <= c.aware(self.clock())+timedelta(seconds=self.policy.lease_seconds), "LEASE_LIMIT_INVALID")
        return renewed

    def read_current(self, company_ids):
        c.require(type(company_ids) is tuple and 1 <= len(company_ids) <= 10
                  and tuple(sorted(set(company_ids))) == company_ids, "COMPANY_SET_INVALID")
        for cid in company_ids: c.identifier(cid)
        c.require(callable(self.card_factory), "PROFILE_READER_NOT_READY")
        data = self._call("read_current", {"company_ids": list(company_ids)})
        wire.shape(data, {"revision", "policy_hash", "company_ids", "cards", "checkpoint"})
        c.require(c.is_hash(data["revision"]) and data["policy_hash"] == self.policy.fingerprint
                  and data["company_ids"] == list(company_ids), "SNAPSHOT_SCOPE_INVALID")
        c.require(type(data["cards"]) is list and len(data["cards"]) == len(company_ids), "PARTIAL_CARD_READ")
        cards, seen = [], set()
        for row in data["cards"]:
            wire.shape(row, {"company", "properties_json", "read_at", "complete", "manual_ownership_complete"})
            company = wire.decode(row["company"], c.CompanySnapshot)
            c.require(company.company_id in company_ids and company.company_id not in seen, "CARD_SCOPE_INVALID")
            c.require(row["complete"] is True and row["manual_ownership_complete"] is True, "PARTIAL_CARD_READ")
            read_at = wire.timestamp(row["read_at"])
            c.require(timedelta(0) <= c.aware(self.clock())-read_at <= timedelta(minutes=5), "STALE_CARD_READ")
            c.require(type(c.parse_json(row["properties_json"])) is dict, "CARD_PROPERTIES_INVALID")
            card = self.card_factory(company, row["properties_json"], read_at, True)
            c.require(card.company.company_id == company.company_id and card.complete is True
                      and card.read_at == read_at and card.properties_json == row["properties_json"], "CARD_FACTORY_INVALID")
            cards.append(card); seen.add(company.company_id)
        checkpoint = wire.decode_checkpoint(data["checkpoint"])
        wire.scope_checkpoint(checkpoint, company_ids)
        self.snapshots[data["revision"]] = (company_ids, wire.decode_checkpoint(wire.encode_checkpoint(checkpoint)))
        return data["revision"], tuple(cards), checkpoint

    def commit_batch(self, expected_revision, prepared):
        c.require(expected_revision in self.snapshots, "SNAPSHOT_NOT_READ")
        ids, previous = self.snapshots[expected_revision]
        wire.shape(prepared, {"jobs", "existing_keys", "seen_triggers", "section_inputs", "profile_hashes",
                              "unresolved_companies", "checks", "external_calls", "persistent_write_performed", "assessments"})
        c.require(type(prepared["external_calls"]) is int and prepared["external_calls"] == 0 and prepared["persistent_write_performed"] is False
                  and not prepared["unresolved_companies"], "PREPARED_BATCH_INVALID")
        jobs = prepared["jobs"]
        c.require(type(jobs) is tuple and len(jobs) <= 100 and all(type(j) is c.JobSpec for j in jobs), "JOBS_INVALID")
        keys = set()
        for j in jobs:
            c.require(j.kind == "ENRICH_SECTION" and j.company_id in ids and j.policy_hash == self.policy.fingerprint
                      and j.policy_version == self.policy.version and j.job_key not in keys, "JOB_SCOPE_INVALID")
            keys.add(j.job_key)
        checkpoint = {k: prepared[k] for k in wire.CP_KEYS if k != "section_states"}
        checkpoint["section_states"] = previous["section_states"]
        encoded = wire.encode_checkpoint(checkpoint); wire.scope_checkpoint(checkpoint, ids)
        c.require(previous["existing_keys"] <= checkpoint["existing_keys"]
                  and keys <= checkpoint["existing_keys"] and previous["seen_triggers"] <= checkpoint["seen_triggers"],
                  "CHECKPOINT_HISTORY_LOST")
        c.require(checkpoint["existing_keys"] <= previous["existing_keys"] | keys, "UNBACKED_JOB_KEY")
        fresh = checkpoint["seen_triggers"] - previous["seen_triggers"]
        c.require(fresh <= self.triggers.keys(), "TRIGGER_RECEIPTS_REQUIRED")
        receipts = [self.triggers[k] for k in sorted(fresh)]
        c.require(all(t["company_id"] in ids for t in receipts), "TRIGGER_SCOPE_INVALID")
        body = {"revision": expected_revision, "company_ids": list(ids), "jobs": [wire.encode(j) for j in jobs],
                "checkpoint": encoded, "triggers": receipts}
        key = c.digest(body)
        self.last_batch = key
        try:
            result = self._call("commit_batch", body | {"batch_key": key}, mutating=True)
        except UnknownCommit:
            return self.reconcile_batch(key) or "UNKNOWN"
        if result == {"state": "CONFLICT"}:
            return "CONFLICT"
        if result != {"state": "APPLIED", "batch_key": key}:
            return self.reconcile_batch(key) or "UNKNOWN"
        return "APPLIED"

    def reconcile_batch(self, key):
        c.require(c.is_hash(key), "BATCH_KEY_INVALID")
        try:
            result = self._call("lookup_batch", {"batch_key": key})
        except (BackendFault, c.ContractError):
            return None
        return "APPLIED" if result == {"state": "APPLIED", "batch_key": key} else None

    def read_registry_page(self, *, after=None, limit=100):
        c.require(type(limit) is int and 1 <= limit <= 100, "REGISTRY_LIMIT_INVALID")
        if after is not None: c.identifier(after)
        data = self._call("read_registry_page", {"after": after, "limit": limit})
        wire.shape(data, {"companies", "checkpoint", "has_more", "revision", "state", "after"})
        rows = tuple(wire.decode(x, c.CompanySnapshot) for x in data["companies"])
        ids = tuple(r.company_id for r in rows)
        actual_after=data['after']
        if actual_after is not None:c.identifier(actual_after)
        c.require(after is None or after==actual_after,'REGISTRY_CURSOR_CHANGED')
        c.require(len(rows) <= limit and ids == tuple(sorted(set(ids))) and all(actual_after is None or x > actual_after for x in ids),
                  "REGISTRY_ORDER_INVALID")
        c.require(type(data["has_more"]) is bool and data["checkpoint"] == (ids[-1] if ids else actual_after),
                  "REGISTRY_CHECKPOINT_INVALID")
        cp=wire.decode_checkpoint(data['state']);wire.scope_checkpoint(cp,ids)
        c.require(c.is_hash(data['revision']) if rows else data['revision'] is None,'REGISTRY_REVISION_INVALID')
        page={'companies':rows,'checkpoint':data['checkpoint'],'has_more':data['has_more'],
              'revision':data['revision'],'state':cp,'after':actual_after}
        c.require(rows or data['has_more'] is False,'REGISTRY_CHECKPOINT_INVALID')
        if rows:self.registry_pages[data['revision']]=self._page_hash(page)
        return page

    @staticmethod
    def _page_hash(page):
        return c.digest({**page,'companies':[wire.encode(r) for r in page['companies']],
                         'state':wire.encode_checkpoint(page['state'])})

    def plan_registry_page(self,page,jobs):
        wire.shape(page,{'companies','checkpoint','has_more','revision','state','after'})
        ids=tuple(r.company_id for r in page['companies'])
        c.require(1<=len(ids)<=100 and c.is_hash(page['revision']) and type(jobs) is tuple
                  and len(jobs)<=1000,'REGISTRY_PAGE_INVALID')
        c.require(self.registry_pages.get(page['revision'])==self._page_hash(page),'REGISTRY_PAGE_CHANGED')
        c.require(all(type(j) is c.JobSpec and j.kind=='ENRICH_SECTION' and j.company_id in ids
                      and j.policy_hash==self.policy.fingerprint and j.policy_version==self.policy.version for j in jobs),
                  'JOB_SCOPE_INVALID')
        c.require(len({j.job_key for j in jobs})==len(jobs),'JOB_DUPLICATE')
        cp=wire.decode_checkpoint(wire.encode_checkpoint(page['state']))
        cp['existing_keys']=frozenset(set(cp['existing_keys'])|{j.job_key for j in jobs})
        body={'revision':page['revision'],'company_ids':list(ids),'jobs':[wire.encode(j) for j in jobs],
              'checkpoint':wire.encode_checkpoint(cp),'triggers':[],
              'sweep_after':page['after'],'sweep_next':page['checkpoint'] if page['has_more'] else None}
        key=c.digest(body);self.last_batch=key
        try:result=self._call('plan_page',body|{'batch_key':key},mutating=True)
        except UnknownCommit:return self.reconcile_batch(key) or 'UNKNOWN'
        if result=={'state':'CONFLICT'}:return 'CONFLICT'
        return 'APPLIED' if result=={'state':'APPLIED','batch_key':key} else self.reconcile_batch(key) or 'UNKNOWN'
