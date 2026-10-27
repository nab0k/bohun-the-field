"""One bounded cycle and a resumable foreground loop; dry/observe by default."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import threading
import time
from urllib.parse import urlencode
import uuid

from . import VERSION
from .collectors import Bridge, load_manager
from .harvest import discover
from .health import evaluate, optional_connect, read_delivery
from .policy import Problem, assess, digest, identity_url, load_policy, parse_json, read_file, require, stamp
from .radars import Budget, HTTP, artifact_page, radar_pages
from .store import Store


def code_identity():
    directory = Path(__file__).parent
    return {"version": VERSION, "code_hash": digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.glob("*.py"))}), "integration": "REPOSITORY_PACKAGE"}


class Worker:
    def __init__(self, store, policy, policy_hash, config, *, clock=time.time,
                 monotonic=time.monotonic, http_factory=None, token_provider=None,
                 manager=None, delivery_reader=None):
        self.store, self.policy, self.policy_hash, self.config = store, policy, policy_hash, config
        self.clock, self.monotonic = clock, monotonic
        self.http_factory, self.token_provider = http_factory, token_provider
        self.manager, self.delivery_reader = manager, delivery_reader
        self.producer = code_identity()
        if manager is not None:
            self.producer["manager_sha256"] = hashlib.sha256(read_file(Path(manager.__file__))).hexdigest()

    def cycle(self, *, apply=False, approved_policy_hash=None):
        if apply:
            require(self.config.get("writer_enabled") is True and self.config.get("live_inputs") is True
                    and approved_policy_hash == self.policy_hash, "APPLY_NOT_ADMITTED")
            require(self.manager is not None and self.delivery_reader is not None and
                    self.config.get("collector_ready") is True, "COLLECTOR_OR_DELIVERY_UNAVAILABLE")
        started = self.clock()
        run = self.store.start_run(started)
        self.store.set_meta("current_run", run)
        self.store.set_meta("policy_sha256", self.policy_hash)
        self.store.set_meta("producer", json.dumps(self.producer, sort_keys=True))
        budget = Budget(self.policy["limits"], monotonic=self.monotonic)
        def api_reservation(key):
            self.store.reserve(key, run, "discovery_api", self.clock(),
                               day_limit=self.policy["limits"]["max_api_day"],
                               cycle_limit=self.policy["limits"]["max_requests"])
        http = self.http_factory(budget, api_reservation) if self.http_factory else HTTP(
            budget, enabled=self.config.get("live_inputs") is True, reserve_api=api_reservation)
        token = self.token_provider or (lambda: oauth_token(http, self.config))
        report = {"run_id": run, "observed_at": stamp(started), "producer": self.producer,
                  "policy_sha256": self.policy_hash, "mode": "APPLY" if apply else "OBSERVE",
                  "execution": "INJECTED_TRANSPORT_SIMULATION" if self.http_factory else
                      "NETWORK_ENABLED" if self.config.get("live_inputs") else "OFFLINE",
                  "providers": {}, "validation": [], "connections": [], "health": [], "errors": [],
                  "supabase_writes": 0, "new_subscriptions": 0, "live_proof": "NOT_ESTABLISHED_BY_CODE"}
        bridge = Bridge(self.manager, http, token, self.store, self.policy, run, self.clock,
                        live_enabled=apply, pilot_proofs=self.config.get("pilot_proofs")) if self.manager else None
        try:
            # Artifact imports work offline; their cursors are independent of radar cursors.
            for source in self.config.get("artifacts", []):
                if source.get("enabled") is False:
                    continue
                sid = source["id"]
                try:
                    rows, cursor = artifact_page(source, self.store.cursor(sid), self.policy["limits"]["max_hits"])
                    count = self.store.ingest(sid, source["query_id"], rows, cursor, self.clock())
                    report["providers"][sid] = {"new_observations": count, "coverage": cursor["coverage"]}
                except Problem as exc:
                    report["providers"][sid] = {"error": str(exc)}
            if self.config.get("live_inputs"):
                for source in self.config.get("providers", []):
                    if not source.get("enabled"):
                        continue
                    sid = source["id"]
                    cursor = self.store.cursor(sid)
                    if cursor.get("retry_at", 0) > self.clock():
                        report["providers"][sid] = {"state": "BACKOFF"}
                        continue
                    count = 0
                    try:
                        for rows, state in radar_pages(source, cursor, http, token, self.policy["limits"]):
                            count += self.store.ingest(sid, source["query_id"], rows, state, self.clock())
                            cursor = state
                        report["providers"][sid] = {"new_observations": count, "coverage": cursor.get("coverage", "UNKNOWN")}
                    except Problem as exc:
                        # Preserve every earlier successfully committed page.
                        state = self.store.cursor(sid)
                        state["retry_at"] = self.clock() + 3600
                        state["error"] = str(exc)
                        self.store.ingest(sid, source["query_id"], [], state, self.clock())
                        report["providers"][sid] = {"new_observations": count, "error": str(exc), "coverage": "UNKNOWN"}
                        if str(exc) == "DNS_TIMEOUT":
                            report["errors"].append("DNS_TIMEOUT")
                        if source["type"] == "inoreader" and any(code in str(exc) for code in ("_429", "_401", "_403", "QUOTA_BLOCKED")):
                            report["errors"].append("INOREADER_WRITER_BLOCKED")

                # Revalidate pending profiles before spending more slots on new candidates.
                due = self.store.due(self.clock(), self.policy["limits"]["max_sites"])
                refresh = []
                for profile in self.store.profiles():
                    if profile.get("lifecycle") == "VALIDATED" and profile["assessment"]["decision"] != "REJECT" and \
                            (self.clock() - profile["validated_at"] > 300 or profile.get("policy_sha256") != self.policy_hash):
                        refresh.append({"id": profile["candidate_id"], "url": profile["feed_url"],
                                        "payload": json.dumps({"publisher_hints": profile.get("publisher_hints", [])})})
                selected = list({r["id"]: r for r in [*refresh, *due]}.values())[:self.policy["limits"]["max_sites"]]
                for candidate in selected:
                    try:
                        feeds = discover(candidate["url"], http, self.clock())
                        hints = json.loads(candidate["payload"]).get("publisher_hints", []) if "payload" in candidate else []
                        for feed in feeds:
                            feed["publisher_hints"] = hints
                            feed["assessment"] = assess(feed, self.policy, self.clock())
                            feed["policy_sha256"] = self.policy_hash
                            feed["producer"] = self.producer
                        self.store.validate(candidate["id"], feeds, self.clock())
                        report["validation"].append({"candidate_id": candidate["id"], "profiles": len(feeds),
                                                     "decisions": [f["assessment"]["decision"] for f in feeds]})
                        for feed in feeds:
                            if feed["assessment"]["decision"] == "REVIEW":
                                self.store.event("REVIEW", digest(feed["feed_url"]),
                                                 {"rules": feed["assessment"]["rule_ids"]}, self.clock())
                    except Problem as exc:
                        reason = str(exc)
                        review = "REVIEW" in reason or "ROBOTS" in reason or "ACCESS" in reason
                        self.store.fail(candidate["id"], reason, self.clock(), review=review)
                        report["validation"].append({"candidate_id": candidate["id"], "error": reason})
                        if reason in ("HTTP_REQUEST_CAP", "HTTP_BYTE_CAP", "CYCLE_TIME_CAP", "DNS_TIMEOUT"):
                            report["errors"].append(reason)
                            break

            if apply:
                writer_profile = "writer"
                try:
                    require(not report["errors"], "CYCLE_BUDGET_WRITER_HOLD")
                    require(bridge.reconcile(), "UNRESOLVED_WRITE_HOLD")
                    for profile in self.store.profiles():
                        if profile.get("lifecycle") != "VALIDATED" or profile["assessment"]["decision"] != "AUTO_ADMIT":
                            continue
                        writer_profile = profile["profile_id"]
                        require(profile.get("policy_sha256") == self.policy_hash, "POLICY_REVALIDATION_REQUIRED")
                        # Verify the actual destination reader before allowing a new subscription.
                        prospective = dict(profile)
                        prospective["connection"] = {"connected_at": self.clock(),
                                                       "folder": self.manager.FOLDERS[profile["assessment"]["group"]]}
                        receipt = self.delivery_reader(prospective)
                        require(receipt.get("read_only") is True and
                                receipt.get("origin_stream_id") == "feed/" + profile["feed_url"] and
                                receipt.get("folder") == prospective["connection"]["folder"] and
                                isinstance(receipt.get("records"), list) and len(receipt["records"]) <= 20,
                                "DELIVERY_READER_SCOPE")
                        result = bridge.connect(profile)
                        report["connections"].append({"profile_id": profile["profile_id"], "summary": result["summary"]})
                        report["new_subscriptions"] += result["summary"]["added"]
                        break
                except Problem as exc:
                    report["errors"].append(str(exc))
                    self.store.event("WRITER_HOLD", writer_profile, {"reason": str(exc)}, self.clock())

            for profile in self.store.profiles():
                if profile.get("lifecycle") not in ("CONNECTED", "ACTIVE"):
                    continue
                try:
                    require("INOREADER_WRITER_BLOCKED" not in report["errors"], "INOREADER_AUTH_OR_QUOTA_HOLD")
                    if bridge and self.config.get("live_inputs"):
                        snapshot = bridge.snapshot(bridge.token())
                        sub = snapshot.get("feed/" + profile["feed_url"])
                        require(sub is not None and self.manager.has_folder(sub, profile["connection"]["folder"]),
                                "CONNECTION_LOST")
                        profile["connection"]["verified_at"] = self.clock()
                    receipt = self.delivery_reader(profile) if self.delivery_reader else None
                    status = evaluate(profile, receipt, self.clock())
                    previous = profile.get("delivery", {}).get("state")
                    profile["delivery"] = status
                    profile["lifecycle"] = status["lifecycle"]
                    self.store.put_profile(profile)
                    if receipt:
                        self.store.record_delivery(profile["profile_id"], receipt, self.clock())
                    if previous != status["state"]:
                        self.store.event("HEALTH_CHANGE", profile["profile_id"], {"state": status["state"]}, self.clock())
                    report["health"].append({"profile_id": profile["profile_id"], **status})
                except Problem as exc:
                    report["health"].append({"profile_id": profile["profile_id"], "state": "UNKNOWN", "reason": str(exc)})
        except Problem as exc:
            report["errors"].append(str(exc))
        finally:
            report["http"] = {"requests": budget.requests, "bytes": budget.bytes, "trace": budget.trace}
            report["queue"] = self.store.summary()
            report["writer_pending_reconciliation"] = len(self.store.unresolved())
            report["result"] = "PARTIAL" if report["errors"] else "CYCLE_FINISHED"
            report["finished_at"] = stamp(self.clock())
            self.store.finish_run(run, self.clock(), report)
        return report


def serve(worker, *, stop=None, wait=None, on_report=None, apply=False, approved_policy_hash=None):
    stop = stop if stop is not None else threading.Event()
    wait = wait or stop.wait
    while not stop.is_set():
        report = worker.cycle(apply=apply, approved_policy_hash=approved_policy_hash)
        if on_report:
            on_report(report)
        if "DNS_TIMEOUT" in report["errors"]:
            break  # Don't accumulate unresolved OS resolver threads over later cycles.
        if stop.is_set():
            break
        wait(worker.policy["limits"]["interval_seconds"])


def oauth_token(http, config):
    require(config.get("live_inputs") is True, "OAUTH_DISABLED")
    names = config.get("oauth_env")
    require(names == {"client_id": "INOREADER_CLIENT_ID", "client_secret": "INOREADER_CLIENT_SECRET",
                      "refresh_token": "INOREADER_REFRESH_TOKEN"}, "OAUTH_EXPLICIT_ENV_REQUIRED")
    values = {key: os.environ.get(name) for key, name in names.items()}
    require(all(values.values()), "OAUTH_CREDENTIALS_MISSING")
    body = urlencode({**values, "grant_type": "refresh_token"}).encode()
    response = http.request("https://www.inoreader.com/oauth2/token", method="POST", body=body, api=True,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
    require(response.status == 200, "OAUTH_HTTP_" + str(response.status))
    data = parse_json(response.body)
    require(isinstance(data, dict) and isinstance(data.get("access_token"), str), "OAUTH_CONTRACT")
    require(not data.get("refresh_token") or data["refresh_token"] == values["refresh_token"], "OAUTH_ROTATION_REVIEW")
    return data["access_token"]


def validate_config(config):
    require(isinstance(config, dict) and config.get("version") == 1, "CONFIG_VERSION")
    for name in ("live_inputs", "writer_enabled", "collector_ready"):
        require(type(config.get(name, False)) is bool, "CONFIG_BOOL")
    providers, artifacts = config.get("providers", []), config.get("artifacts", [])
    require(isinstance(providers, list) and len(providers) <= 3 and
            isinstance(artifacts, list) and len(artifacts) <= 8, "CONFIG_INPUT_CAP")
    ids, kinds = set(), set()
    for source in providers + artifacts:
        require(isinstance(source, dict) and source.get("id") and source.get("query_id") and
                type(source.get("enabled", True)) is bool, "CONFIG_SOURCE")
        require(isinstance(source["query_id"], str) and len(source["query_id"]) <= 100 and
                isinstance(source["id"], str) and len(source["id"]) <= 80 and
                all(c.isalnum() or c in "-_" for c in source["id"]), "CONFIG_SOURCE_ID")
        require(source["id"] not in ids, "CONFIG_DUPLICATE_ID")
        ids.add(source["id"])
        if source in providers:
            require(source.get("type") in ("inoreader", "google_alerts", "talkwalker") and
                    source["type"] not in kinds, "CONFIG_RADAR_TYPE")
            kinds.add(source["type"])
            if source.get("enabled"):
                if source["type"] == "inoreader":
                    require(isinstance(source.get("stream"), str) and source["stream"] and
                            len(source["stream"]) <= 1000 and "?" not in source["stream"], "CONFIG_STREAM")
                else:
                    identity_url(source.get("url"), opaque=True)
    return config


def runtime_paths(config):
    """Explicit private storage outside the source checkout; no cwd/env fallback."""
    root_value, state_value = config.get("runtime_root"), config.get("state_path")
    require(isinstance(root_value, str) and bool(root_value), "RUNTIME_ROOT_REQUIRED")
    require(isinstance(state_value, str) and bool(state_value), "PRIVATE_STATE_ROOT")
    root, state = Path(root_value), Path(state_value)
    field = Path(__file__).resolve().parents[3]
    for path in (root, state):
        require(path.is_absolute() and ".." not in path.parts and
                not path.is_relative_to(field) and
                not any(part in {"public", "dist", ".git"} for part in path.parts), "PRIVATE_STATE_ROOT")
        require(not any(p.is_symlink() for p in (path, *path.parents)), "PATH_SYMLINK")
    allowed = root / "sandbox"
    require(state.is_relative_to(allowed) and state != allowed, "PRIVATE_STATE_ROOT")
    require(root.is_dir(), "RUNTIME_ROOT_REQUIRED")
    return allowed, state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("once", "serve"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--bootstrap", action="store_true", help="Explicit new observe-only journal")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-policy-sha256")
    args = parser.parse_args(argv)
    config_path = Path(args.config)
    document = parse_json(read_file(config_path))
    config = validate_config(document.get("runtime", document))
    policy_path = Path(config.get("policy_path") or config_path)
    policy, policy_hash = load_policy(policy_path)
    allowed, state_root = runtime_paths(config)
    require(not args.bootstrap or (args.mode == "once" and not args.apply), "BOOTSTRAP_OBSERVE_ONLY")
    if args.bootstrap:
        require(not any(p.is_symlink() for p in (allowed, *allowed.parents)), "PATH_SYMLINK")
        allowed.mkdir(mode=0o700, exist_ok=True)
    manager = None
    if config.get("manager"):
        manager = load_manager(config["manager"]["field_root"], config["manager"]["sha256"])
    delivery = config.get("delivery", {})
    reader = None
    if delivery.get("enabled"):
        reader = lambda profile: read_delivery(lambda: optional_connect(delivery), delivery, profile)
    with Store(state_root, config["deployment_id"], bootstrap=args.bootstrap) as store:
        worker = Worker(store, policy, policy_hash, config, manager=manager, delivery_reader=reader)
        if args.mode == "once":
            report = worker.cycle(apply=args.apply, approved_policy_hash=args.approved_policy_sha256)
            print(json.dumps(report, ensure_ascii=False))
            return 2 if report["errors"] else 0
        stop = threading.Event()
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            for sig in previous:
                signal.signal(sig, lambda *_: stop.set())
            serve(worker, stop=stop, apply=args.apply, approved_policy_hash=args.approved_policy_sha256,
                  on_report=lambda r: print(json.dumps(r, ensure_ascii=False), flush=True))
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (Problem, OSError, KeyError, TypeError):
        # Don't print config paths, secret URLs, exception chains, or driver diagnostics.
        print(json.dumps({"result": "HOLD", "reason": "CONFIG_STATE_OR_RUNTIME_GUARD"}))
        raise SystemExit(2)
