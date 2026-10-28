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
                 *, live_enabled=False, pilot_proofs=None):
        self.manager, self.http, self.token_provider = manager, http, token_provider
        self.store, self.policy, self.run_id, self.clock = store, policy, run_id, clock
        self.live_enabled = live_enabled
        self.pilot_proofs = pilot_proofs or {}
        self.token_value = None

    def token(self):
        if self.token_value is None:
            self.token_value = self.token_provider()
        return self.token_value

    def snapshot(self, token):
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
                        self._connected(profile, folder, operation["created"], operation["id"])
            else:
                self.store.event("WRITER_HOLD", operation["id"], {"reason": "OUTCOME_REQUIRES_REVIEW"}, self.clock())
        return not self.store.unresolved()

    def _connected(self, profile, folder, now, operation):
        profile["lifecycle"] = "CONNECTED"
        profile["connection"] = {"membership_verified": True, "folder_verified": True,
                                  "connected_at": now, "verified_at": self.clock(),
                                  "folder": folder, "operation_id": operation,
                                  "stream_id": "feed/" + profile["feed_url"]}
        self.store.put_profile(profile)
        self.store.event("CONNECTED", profile["profile_id"], {"folder": folder}, self.clock())

    def connect(self, profile):
        require(self.live_enabled, "WRITER_DISABLED")
        require(self.pilot_proofs.get("single") is True and self.pilot_proofs.get("batch3") is True,
                "PILOT_PROOFS_REQUIRED")
        require(profile["assessment"]["decision"] == "AUTO_ADMIT", "ADMISSION_REQUIRED")
        now = self.clock()
        require(0 <= now - profile["validated_at"] <= self.policy["limits"]["snapshot_ttl"], "VALIDATION_STALE")
        group = profile["assessment"]["group"]
        require(group in self.manager.FOLDERS, "ROUTE_UNKNOWN")
        feed_url = identity_url(profile["feed_url"])
        folder = self.manager.FOLDERS[group]
        require(self.reconcile(), "UNRESOLVED_WRITE_HOLD")
        preflight = self.snapshot(self.token())
        aliases = set(profile.get("aliases", [])) - {feed_url}
        hosts = {urlsplit(url).hostname for url in [feed_url, profile.get("publisher_url", feed_url),
                                                   *profile.get("publisher_hints", [])]}
        for stream in preflight:
            if not stream.startswith("feed/") or stream == "feed/" + feed_url:
                continue
            existing = stream[5:]
            require(existing not in aliases, "EXISTING_ALIAS_REVIEW")
            require(urlsplit(existing).hostname not in hosts, "EXISTING_HOST_PROFILE_REVIEW")
        operation = self.store.begin_operation(profile, self.run_id, now)
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
                kind, day_limit, cycle_limit = "quickadd", self.policy["limits"]["max_add_day"], 1
            elif path == "subscription/edit":
                require(values == {"s": "feed/" + feed_url, "a": "user/-/label/" + folder, "ac": "edit"}
                        and self.store.has_attempt(operation), "FOLDER_TARGET_MISMATCH")
                kind, day_limit, cycle_limit = "folder_edit", self.policy["limits"]["max_folder_day"], 1
            else:
                raise Problem("WRITE_METHOD_DENIED")
            self.store.reserve(operation, self.run_id, kind, when, day_limit=day_limit, cycle_limit=cycle_limit)
            self.store.operation_state(operation, "SENDING_" + kind.upper())
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
            item = report["results"][0]
            if code == 0 and item.get("membership_verified") is True and item.get("folder_verified") is True:
                self.store.operation_state(operation, "CONFIRMED", data)
                self._connected(profile, folder, now, operation)
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
