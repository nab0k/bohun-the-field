"""Explicit bounded pilot admission for the existing writer; no I/O on import."""
from dataclasses import dataclass
import hashlib
import re

from .policy import GROUPS, digest, epoch, identity_url, parse_json, require


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
                value["stage"] in ("single", "batch3"), "PILOT_STAGE")
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

    def selects(self, profile):
        return (profile.get("feed_url"), profile.get("assessment", {}).get("group")) in self.sources

    def admit(self, profile, store, now):
        self.check(now)
        require(store.deployment == self.deployment_id, "PILOT_DEPLOYMENT")
        require(self.selects(profile) and profile.get("policy_sha256") == self.policy_sha256 and
                profile.get("assessment", {}).get("decision") == "AUTO_ADMIT", "PILOT_SOURCE_NOT_ADMITTED")

    def reserve(self, profile, store, run_id, now):
        self.admit(profile, store, now)
        # The same manifest+feed can spend only one attempt, across restarts/days.
        # A reservation lost before quickadd consumes a slot conservatively.
        store.reserve(digest([self.sha256, profile["feed_url"]]), run_id,
                      "pilot:" + self.sha256, now, day_limit=len(self.sources), cycle_limit=1)

    def connected_count(self, profiles):
        return len({(p.get("feed_url"), p.get("assessment", {}).get("group")) for p in profiles
                    if self.selects(p) and p.get("lifecycle") in ("CONNECTED", "ACTIVE") and
                    p.get("policy_sha256") == self.policy_sha256 and
                    p.get("connection", {}).get("membership_verified") is True and
                    p.get("connection", {}).get("folder_verified") is True})


def run_cycles(worker, admission, policy_hash):
    """Each iteration retains the normal one-attempt cap and one shared budget."""
    reports = []
    for _ in admission.sources:
        admission.check(worker.clock())
        worker.shared_budget.remaining()
        report = worker.cycle(apply=True, approved_policy_hash=policy_hash)
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
