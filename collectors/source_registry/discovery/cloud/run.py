"""One cloud-backed cycle; no schedule, credential read or HTTP on import/validate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal

from ..collectors import load_manager
from ..discovery import Worker, runtime_paths, validate_config
from ..health import optional_connect, read_delivery
from ..policy import Problem, digest, load_policy, parse_json, read_file, require
from ..store import CheckpointError, Store
from .state import CloudHold, ENDPOINT, HEX, NAME, StateClient, MAX_SNAPSHOT, MAX_SECONDS
from .oauth import AccessProvider

FIELD = Path(__file__).resolve().parents[4]
PACKAGE = Path(__file__).resolve().parents[1]


def code_pins():
    paths = [*PACKAGE.glob("*.py"), *Path(__file__).parent.glob("*.py"),
             FIELD / "collectors/inoreader/inoreader_source_manager.py",
             FIELD / "collectors/inoreader/sync.py",
             FIELD / "collectors/inoreader/content_contract.py",
             FIELD / "cloudflare/source-discovery/state-core.mjs",
             FIELD / "cloudflare/source-discovery/state-worker.mjs"]
    body = FIELD / "collectors/inoreader/body_backfill.py"
    if body.exists():
        paths.append(body)
    return {str(p.relative_to(FIELD)): hashlib.sha256(read_file(p)).hexdigest() for p in sorted(paths)}


def code_hash():
    return digest(code_pins())


def settings(document, *, apply=False, approved_policy_hash=None):
    require(isinstance(document, dict) and set(document) == {"version", "enabled", "state_endpoint",
        "code_sha256", "policy_sha256", "policy_path", "runtime"}, "CLOUD_CONFIG")
    require(document["version"] == 1 and type(document["enabled"]) is bool, "CLOUD_CONFIG")
    require(isinstance(document["state_endpoint"], str) and
            ENDPOINT.fullmatch(document["state_endpoint"]), "CLOUD_ENDPOINT")
    require(all(isinstance(document[k], str) and HEX.fullmatch(document[k])
                for k in ("code_sha256", "policy_sha256")), "CLOUD_PINS")
    require(document["code_sha256"] == code_hash(), "CLOUD_CODE_PIN")
    policy, policy_hash = load_policy(Path(document["policy_path"]))
    require(policy_hash == document["policy_sha256"], "CLOUD_POLICY_PIN")
    config = validate_config(document["runtime"])
    if config.get("live_inputs") and (config.get("manager") or any(
            p.get("enabled") and p.get("type") == "inoreader" for p in config.get("providers", []))):
        require(config.get("oauth_mode") == "coordinator" and "oauth_env" not in config,
                "CLOUD_COORDINATOR_OAUTH_REQUIRED")
    require(isinstance(config.get("deployment_id"), str) and NAME.fullmatch(config["deployment_id"]), "DEPLOYMENT_ID")
    allowed, root = runtime_paths(config)
    manager = None
    if config.get("manager"):
        require(Path(config["manager"]["field_root"]) == FIELD, "CLOUD_MANAGER_ROOT")
        manager = load_manager(FIELD, config["manager"]["sha256"])
    if apply:
        require(config.get("live_inputs") is True and config.get("writer_enabled") is True and
                config.get("collector_ready") is True and config.get("delivery", {}).get("enabled") is True and
                manager is not None and approved_policy_hash == policy_hash and
                config.get("pilot_proofs", {}).get("single") is True and
                config.get("pilot_proofs", {}).get("batch3") is True, "CLOUD_APPLY_NOT_ADMITTED")
    return config, policy, policy_hash, allowed, root, manager


def restore(raw, allowed, root, deployment):
    require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_SNAPSHOT and
            raw.startswith(b"SQLite format 3\x00"), "CLOUD_SNAPSHOT")
    require(not root.exists(), "CLOUD_LOCAL_STATE_EXISTS")
    # runtime_paths already checks every existing path ancestor for symlinks.
    allowed.mkdir(mode=0o700, exist_ok=True)
    root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.mkdir(mode=0o700)
    marker = root.parent / ("." + deployment + ".deployment.json")
    require(not marker.exists() and not marker.is_symlink(), "CLOUD_LOCAL_STATE_EXISTS")
    with (root / "state.sqlite").open("xb") as output:
        os.chmod(output.name, 0o600)
        output.write(raw)
    with marker.open("x", encoding="utf-8") as output:
        os.chmod(output.name, 0o600)
        json.dump({"deployment": deployment, "format": 1}, output)


def summary(report, apply):
    # Never print a raw Worker receipt: it can contain private provenance/cursors.
    providers, errors = report.get("providers", {}), report.get("errors", [])
    require(isinstance(providers, dict) and isinstance(errors, list), "CLOUD_REPORT")
    failures = len(errors) + sum(isinstance(p, dict) and "error" in p for p in providers.values())
    added = report.get("new_subscriptions", 0)
    require(type(added) is int and 0 <= added <= 5, "CLOUD_REPORT")
    return {"status": "CYCLE_PARTIAL" if failures else "CYCLE_FINISHED",
            "mode": "APPLY" if apply else "OBSERVE", "provider_count": len(providers),
            "error_count": failures, "new_subscriptions": added}


def execute(document, *, mode="once", allow_live=False, apply=False, approved_policy_hash=None,
            transport=None, token_provider=None, worker_factory=Worker):
    require(mode in ("validate", "bootstrap", "once"), "CLOUD_MODE")
    config, policy, policy_hash, allowed, root, manager = settings(
        document, apply=apply, approved_policy_hash=approved_policy_hash)
    if mode == "validate":
        return {"status": "VALIDATED_NO_IO", "mode": "VALIDATE"}
    require(document["enabled"] is True and allow_live, "CLOUD_LIVE_NOT_ALLOWED")
    require(not root.exists(), "CLOUD_LOCAL_STATE_EXISTS")
    if mode == "bootstrap":
        require(not apply and config.get("live_inputs") is False and config.get("writer_enabled") is False and
                not config.get("delivery", {}).get("enabled"), "CLOUD_BOOTSTRAP_OBSERVE_ONLY")
    provider = token_provider if token_provider is not None else lambda: os.environ.get("SOURCE_DISCOVERY_STATE_TOKEN")
    client = StateClient(document["state_endpoint"], config["deployment_id"], document["code_sha256"],
                         policy_hash, provider, enabled=True, transport=transport)
    if mode == "bootstrap":
        allowed.mkdir(mode=0o700, exist_ok=True)
        root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with Store(root, config["deployment_id"], bootstrap=True) as store:
            client.bootstrap(store.db.serialize())
        return {"status": "BOOTSTRAPPED_NO_SOURCE_IO", "mode": "BOOTSTRAP"}
    client.acquire()
    restore(client.download(), allowed, root, config["deployment_id"])
    # Store verifies SQLite integrity, schema and deployment before starting Worker.
    delivery = config.get("delivery", {})
    reader = (lambda profile: read_delivery(lambda: optional_connect(delivery), delivery, profile)) \
        if delivery.get("enabled") else None
    with Store(root, config["deployment_id"], checkpoint=client.checkpoint) as store:
        worker = worker_factory(store, policy, policy_hash, config, manager=manager, delivery_reader=reader,
            token_provider_factory=lambda http: AccessProvider(client, http, scope="read write" if apply else "read"))
        report = worker.cycle(apply=apply, approved_policy_hash=approved_policy_hash)
        safe = summary(report, apply)
        store.sync_checkpoint()
    # Deliberately no finally-release on any uncertain state or interrupted write.
    client.release()
    return safe


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("validate", "bootstrap", "once"))
    parser.add_argument("--config", required=True)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-policy-sha256")
    args = parser.parse_args(argv)
    previous = None
    try:
        document = parse_json(read_file(Path(args.config)))
        if args.mode != "validate":
            def halt(*_):
                raise CloudHold("CLOUD_DEADLINE_OR_SIGNAL_HOLD")
            previous = {sig: signal.getsignal(sig) for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
            for sig in previous:
                signal.signal(sig, halt)
            signal.alarm(MAX_SECONDS)
        result = execute(document, mode=args.mode, allow_live=args.allow_live, apply=args.apply,
                         approved_policy_hash=args.approved_policy_sha256)
        print(json.dumps(result, sort_keys=True))
        return 2 if result["status"] == "CYCLE_PARTIAL" else 0
    except BaseException:
        # Unexpected adapter/driver exceptions may contain secrets too. Never trace them in public logs.
        print(json.dumps({"status": "HOLD", "reason": "CLOUD_CONFIG_STATE_OR_RUNTIME_GUARD"}))
        return 2
    finally:
        if previous is not None:
            signal.alarm(0)
            for sig, handler in previous.items():
                signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
