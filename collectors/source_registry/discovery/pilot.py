"""Explicit bounded pilot admission for the existing writer; no I/O on import."""
from dataclasses import dataclass
import hashlib
import re

from .policy import GROUPS, Problem, digest, epoch, identity_url, parse_json, require


@dataclass(frozen=True)
class PilotAdmission:
    sha256: str
    stage: str
    deployment_id: str
    code_sha256: str
    policy_sha256: str
    expires_at: float
    sources: tuple

    @classmethod
    def load(cls, raw, approved_hash, *, deployment, code_hash, policy_hash, now):
        require(isinstance(approved_hash, str) and
                re.fullmatch(r"[0-9a-f]{64}", approved_hash) is not None and
                hashlib.sha256(raw).hexdigest() == approved_hash, "PILOT_MANIFEST_HASH")
        value = parse_json(raw, limit=16384)
        require(isinstance(value, dict) and set(value) == {"version", "stage", "deployment_id",
            "code_sha256", "policy_sha256", "expires_at", "sources", "max_attempts"}, "PILOT_MANIFEST")
        require(type(value["version"]) is int and value["version"] == 1 and
                value["stage"] in ("single", "batch3", "batch3_reviewed"), "PILOT_STAGE")
        count = 1 if value["stage"] == "single" else 3
        require(type(value["max_attempts"]) is int and value["max_attempts"] == count and
                isinstance(value["sources"], list) and len(value["sources"]) == count, "PILOT_CAP")
        require(value["deployment_id"] == deployment and value["code_sha256"] == code_hash and
                value["policy_sha256"] == policy_hash, "PILOT_PINS")
        sources = []
        for source in value["sources"]:
            require(isinstance(source, dict) and set(source) == {"feed_url", "group"} and
                    source["group"] in GROUPS.values(), "PILOT_SOURCE")
            url = identity_url(source["feed_url"])
            require(url == source["feed_url"] and url not in {s[0] for s in sources}, "PILOT_DUPLICATE")
            sources.append((url, source["group"]))
        result = cls(approved_hash, value["stage"], deployment, code_hash, policy_hash,
                     epoch(value["expires_at"]), tuple(sources))
        result.check(now)
        return result

    def check(self, now):
        require(0 < self.expires_at - now <= 86400, "PILOT_EXPIRED_OR_TOO_LONG")

    @property
    def reviewed(self):
        return self.stage == "batch3_reviewed"

    def group_for(self, profile):
        if self.reviewed:
            return dict(self.sources).get(profile.get("feed_url"))
        return profile.get("assessment", {}).get("group")

    def selects(self, profile):
        return (profile.get("feed_url"), self.group_for(profile)) in self.sources

    def admit(self, profile, store, now):
        self.check(now)
        require(store.deployment == self.deployment_id, "PILOT_DEPLOYMENT")
        require(self.selects(profile) and profile.get("policy_sha256") == self.policy_sha256, "PILOT_SOURCE_NOT_ADMITTED")
        assessment = profile.get("assessment", {})
        group = self.group_for(profile)
        if self.reviewed and assessment.get("decision") == "REVIEW":
            rules = assessment.get("rule_ids")
            require(isinstance(rules, list) and bool(rules) and all(isinstance(rule, str) for rule in rules),
                    "PILOT_REVIEW_RULES_REQUIRED")
            blockers = {rule for rule in rules if rule.startswith("gate:")}
            require(blockers <= {"gate:domain-signal-ratio"} and assessment.get("group") in (None, group)
                    and profile.get("access") == "PUBLIC" and profile.get("comment_feed") is False,
                    "PILOT_REVIEW_HARD_GATE")
        else:
            require(assessment.get("decision") == "AUTO_ADMIT" and assessment.get("group") == group,
                    "PILOT_SOURCE_NOT_ADMITTED")
        return group

    def reserve(self, profile, store, run_id, now):
        self.admit(profile, store, now)
        # The same manifest+feed can spend only one attempt, across restarts/days.
        # A reservation lost before quickadd consumes a slot conservatively.
        store.reserve(digest([self.sha256, profile["feed_url"]]), run_id,
                      "pilot:" + self.sha256, now, day_limit=len(self.sources), cycle_limit=1)

    def check_profiles(self, store, now):
        selected = [profile for profile in store.profiles() if self.selects(profile)]
        require(len(selected) == len(self.sources), "PILOT_SOURCE_NOT_ADMITTED")
        for profile in selected:
            require(profile.get("lifecycle") in ("VALIDATED", "CONNECTED", "ACTIVE"), "PILOT_SOURCE_NOT_ADMITTED")
            self.admit(profile, store, now)
            if self.reviewed and profile.get("lifecycle") in ("CONNECTED", "ACTIVE"):
                require(profile.get("connection", {}).get("pilot_sha256") == self.sha256,
                        "PILOT_ALREADY_SUBSCRIBED")

    def connected_count(self, profiles):
        return len({(p.get("feed_url"), self.group_for(p)) for p in profiles
                    if self.selects(p) and p.get("lifecycle") in ("CONNECTED", "ACTIVE") and
                    p.get("policy_sha256") == self.policy_sha256 and
                    (not self.reviewed or p.get("connection", {}).get("pilot_sha256") == self.sha256) and
                    p.get("connection", {}).get("membership_verified") is True and
                    p.get("connection", {}).get("folder_verified") is True})


def run_cycles(worker, admission, policy_hash, *, quota=None):
    """Each iteration retains the normal one-attempt cap and one shared budget."""
    reports = []
    snapshot_cache = {} if admission.stage != "single" else None
    if snapshot_cache is not None:
        try:
            admission.check_profiles(worker.store, worker.clock())
            require(not worker.store.unresolved(), "UNRESOLVED_WRITE_HOLD")
            remaining = len(admission.sources) - admission.connected_count(worker.store.profiles())
            if quota is not None:
                quota.require_capacity(reads=1 + remaining, writes=2 * remaining)
        except Problem as exc:
            return [{"result": "PARTIAL", "errors": [str(exc)]}], False
    for _ in admission.sources:
        admission.check(worker.clock())
        worker.shared_budget.remaining()
        report = worker.cycle(apply=True, approved_policy_hash=policy_hash, pilot_snapshot=snapshot_cache)
        reports.append(report)
        if report.get("result") != "CYCLE_FINISHED" or worker.store.unresolved():
            break
        if admission.connected_count(worker.store.profiles()) == len(admission.sources):
            break
        # No connection means a missing, unvalidated or REVIEW profile. Do not
        # loop until a source happens to become eligible in the same invocation.
        if not report.get("connections"):
            break
    connected = admission.connected_count(worker.store.profiles())
    complete = connected == len(admission.sources) and bool(reports) and \
        all(r.get("result") == "CYCLE_FINISHED" for r in reports)
    worker.store.event("PILOT_RESULT", admission.sha256,
        {"stage": admission.stage, "connections_confirmed": connected,
         "complete": complete, "delivery_proof": "NOT_ESTABLISHED"}, worker.clock())
    return reports, complete
