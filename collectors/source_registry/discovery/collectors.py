"""Durable guard around the existing Source Manager, not another quickadd engine."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

from .policy import Problem, identity_url, parse_json, read_file, require
from .radars import api_json
from .pilot import PilotAdmission


def load_manager(field_root, expected_hash):
    field_root = Path(field_root)
    require(field_root.is_absolute(), "FIELD_PATH")
    path = field_root / "collectors/inoreader/inoreader_source_manager.py"
    raw = read_file(path)
    require(hashlib.sha256(raw).hexdigest() == expected_hash, "MANAGER_HASH_MISMATCH")
    previous = list(sys.path)
    try:
        sys.path.insert(0, str(field_root))
        spec = importlib.util.spec_from_file_location("collectors.inoreader._discovery_manager", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = previous


class Bridge:
    def __init__(self, manager, http, token_provider, store, policy, run_id, clock,
                 *, live_enabled=False, pilot_proofs=None, pilot_admission=None, snapshot_cache=None):
        self.manager, self.http, self.token_provider = manager, http, token_provider
        self.store, self.policy, self.run_id, self.clock = store, policy, run_id, clock
        self.live_enabled = live_enabled
        self.pilot_proofs = pilot_proofs or {}
        require(pilot_admission is None or isinstance(pilot_admission, PilotAdmission), "PILOT_ADMISSION_TYPE")
        self.pilot_admission = pilot_admission
        self.snapshot_cache = snapshot_cache
        self.token_value = None

    def token(self):
        if self.token_value is None:
            self.token_value = self.token_provider()
        return self.token_value

    def snapshot(self, token):
        cache = self.snapshot_cache
        if cache:
            require(0 <= self.clock() - cache["at"] <= self.policy["limits"]["snapshot_ttl"],
                    "PILOT_SUBSCRIPTION_SNAPSHOT_STALE")
            return cache["subscriptions"]
        response = api_json(self.http, "subscription/list", token)
        body = parse_json(response.body)
        require(isinstance(body, dict) and isinstance(body.get("subscriptions"), list)
                and len(body["subscriptions"]) <= 2000, "SNAPSHOT_CONTRACT")
        snapshot = {}
        for sub in body["subscriptions"]:
            require(isinstance(sub, dict) and isinstance(sub.get("id"), str) and sub["id"]
                    and isinstance(sub.get("categories", []), list)
                    and all(isinstance(c, dict) for c in sub.get("categories", [])), "SNAPSHOT_ITEM")
            require(sub["id"] not in snapshot, "SNAPSHOT_DUPLICATE")
            snapshot[sub["id"]] = sub
        if cache is not None:
            cache.update(at=self.clock(), subscriptions=snapshot)
        return snapshot

    def reconcile(self):
        pending = self.store.unresolved()
        if not pending:
            return True
        snapshot = None
        for operation in pending:
            if not self.store.has_attempt(operation["id"]):
                self.store.operation_state(operation["id"], "CANCELLED")
                continue
            if snapshot is None:
                snapshot = self.snapshot(self.token())
            data = json.loads(operation["payload"])
            folder = self.manager.FOLDERS.get(data["group"])
            sub = snapshot.get("feed/" + data["feed_url"])
            if sub is not None and folder and self.manager.has_folder(sub, folder):
                self.store.operation_state(operation["id"], "RECONCILED")
                for profile in self.store.profiles():
                    if profile["profile_id"] == operation["profile_id"]:
                        self._connected(profile, folder, operation["created"], operation["id"],
                                        pilot_sha256=data.get("pilot_sha256"))
            else:
                self.store.event("WRITER_HOLD", operation["id"], {"reason": "OUTCOME_REQUIRES_REVIEW"}, self.clock())
        return not self.store.unresolved()

    def _connected(self, profile, folder, now, operation, *, pilot_sha256=None):
        profile["lifecycle"] = "CONNECTED"
        profile["connection"] = {"membership_verified": True, "folder_verified": True,
                                  "connected_at": now, "verified_at": self.clock(),
                                  "folder": folder, "operation_id": operation,
                                  "stream_id": "feed/" + profile["feed_url"]}
        if pilot_sha256 is not None:
            profile["connection"]["pilot_sha256"] = pilot_sha256
        self.store.put_profile(profile)
        self.store.event("CONNECTED", profile["profile_id"], {"folder": folder}, self.clock())

    def check_subscription_conflicts(self, profile, subscriptions, *, require_new=False):
        feed_url = identity_url(profile["feed_url"])
        stream_id = "feed/" + feed_url
        if require_new:
            require(stream_id not in subscriptions, "PILOT_ALREADY_SUBSCRIBED")
        aliases = set(profile.get("aliases", [])) - {feed_url}
        hosts = {urlsplit(url).hostname for url in [feed_url, profile.get("publisher_url", feed_url),
                                                   *profile.get("publisher_hints", [])]}
        for stream in subscriptions:
            if not stream.startswith("feed/") or stream == stream_id:
                continue
            existing = stream[5:]
            require(existing not in aliases, "EXISTING_ALIAS_REVIEW")
            require(urlsplit(existing).hostname not in hosts, "EXISTING_HOST_PROFILE_REVIEW")

    def connect(self, profile):
        require(self.live_enabled, "WRITER_DISABLED")
        now = self.clock()
        if self.pilot_admission is not None:
            group = self.pilot_admission.admit(profile, self.store, now)
        else:
            require(self.pilot_proofs.get("batch3") is True,
                    "PILOT_PROOFS_REQUIRED")
            require(profile["assessment"]["decision"] == "AUTO_ADMIT", "ADMISSION_REQUIRED")
            group = profile["assessment"]["group"]
        require(0 <= now - profile["validated_at"] <= self.policy["limits"]["snapshot_ttl"], "VALIDATION_STALE")
        require(group in self.manager.FOLDERS, "ROUTE_UNKNOWN")
        feed_url = identity_url(profile["feed_url"])
        folder = self.manager.FOLDERS[group]
        require(self.reconcile(), "UNRESOLVED_WRITE_HOLD")
        self.check_subscription_conflicts(profile, self.snapshot(self.token()),
                                          require_new=self.snapshot_cache is not None)
        pilot_sha256 = self.pilot_admission.sha256 if self.pilot_admission is not None else None
        operation = self.store.begin_operation(profile, self.run_id, now, group=group, pilot_sha256=pilot_sha256)
        directory = self.store.root / "operations" / operation
        directory.mkdir(parents=True, mode=0o700)
        name = "Candidate " + profile["profile_id"][:16]
        source = {"name": name, "url": feed_url, "group": group}
        registry, report_path = directory / "registry.json", directory / "manager-report.json"
        registry.write_text(json.dumps({"sources": [source]}), encoding="utf-8")
        args = SimpleNamespace(registry=str(registry), report=str(report_path), workers=1,
                               apply=True, only=name, max_add=1, check_subscriptions=True)

        def discoverer(selected):
            require(selected == source, "MANAGER_SELECTION_MISMATCH")
            return {**selected, "feed_url": feed_url, "status": "validated_feed"}

        def writer(path, token, values):
            when = self.clock()
            require(when - now <= self.policy["limits"]["snapshot_ttl"], "WRITE_PREFLIGHT_STALE")
            if path == "subscription/quickadd":
                require(values == {"quickadd": feed_url}, "WRITE_TARGET_MISMATCH")
                if self.pilot_admission is not None:
                    self.pilot_admission.reserve(profile, self.store, self.run_id, when)
                kind, day_limit, cycle_limit = "quickadd", self.policy["limits"]["max_add_day"], 1
            elif path == "subscription/edit":
                if self.pilot_admission is not None:
                    self.pilot_admission.admit(profile, self.store, when)
                require(values == {"s": "feed/" + feed_url, "a": "user/-/label/" + folder, "ac": "edit"}
                        and self.store.has_attempt(operation), "FOLDER_TARGET_MISMATCH")
                kind, day_limit, cycle_limit = "folder_edit", self.policy["limits"]["max_folder_day"], 1
            else:
                raise Problem("WRITE_METHOD_DENIED")
            self.store.reserve(operation, self.run_id, kind, when, day_limit=day_limit, cycle_limit=cycle_limit)
            self.store.operation_state(operation, "SENDING_" + kind.upper())
            # A post-write readback must never reuse a pre-write snapshot.
            if self.snapshot_cache is not None:
                self.snapshot_cache.clear()
            response = api_json(self.http, path, token, form=values)
            return response.body.decode("utf-8")

        try:
            # Never mutate module globals; other callers retain the CLI's default transports.
            with contextlib.redirect_stdout(io.StringIO()):
                code = self.manager.run(args, discoverer=discoverer, token_provider=self.token,
                                        snapshot_reader=self.snapshot, writer=writer)
            report = parse_json(read_file(report_path))
            data = {"feed_url": feed_url, "group": group, "receipt": report,
                    "policy_sha256": profile.get("policy_sha256"), "producer": profile.get("producer", "UNKNOWN")}
            if pilot_sha256 is not None:
                data["pilot_sha256"] = pilot_sha256
            item = report["results"][0]
            if code == 0 and item.get("membership_verified") is True and item.get("folder_verified") is True:
                self.store.operation_state(operation, "CONFIRMED", data)
                self._connected(profile, folder, now, operation, pilot_sha256=pilot_sha256)
                return report
            self.store.operation_state(operation, "UNKNOWN" if self.store.has_attempt(operation) else "CANCELLED", data)
            raise Problem("CONNECTION_NOT_CONFIRMED")
        except BaseException:
            # Includes a killed caller between send and report persistence.
            if self.store.has_attempt(operation):
                row = next((o for o in self.store.unresolved() if o["id"] == operation), None)
                if row is not None:
                    self.store.operation_state(operation, "UNKNOWN")
            else:
                self.store.operation_state(operation, "CANCELLED")
            raise
