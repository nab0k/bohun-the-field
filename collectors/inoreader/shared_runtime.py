"""Opt-in collector entry point; existing sync.py CLI and workflow stay unchanged."""
import argparse
import json
import os
from pathlib import Path
import signal
import time
import urllib.request

from . import sync
from ..source_registry.discovery.cloud.oauth import AccessProvider
from ..source_registry.discovery.cloud.run import restore, settings
from ..source_registry.discovery.cloud.shared import SharedQuota, api_zone
from ..source_registry.discovery.cloud.state import StateClient, MAX_SECONDS
from ..source_registry.discovery.policy import Problem, parse_json, read_file, require
from ..source_registry.discovery.radars import Budget
from ..source_registry.discovery.store import CheckpointError, Store


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


class CollectorSession:
    def __init__(self, document, *, token_provider, transport=None, clock=time.time):
        self.config, _, self.policy_hash, self.allowed, self.root, _ = settings(document)
        require(document["enabled"] is True and self.config.get("api_quota_mode") == "shared" and
                self.config.get("oauth_mode") == "coordinator", "COLLECTOR_SHARED_NOT_ENABLED")
        require(not self.root.exists(), "CLOUD_LOCAL_STATE_EXISTS")
        self.client = StateClient(document["state_endpoint"], self.config["deployment_id"],
            document["code_sha256"], self.policy_hash, token_provider, enabled=True, transport=transport)
        self.clock, self.store, self.held = clock, None, False

    def __enter__(self):
        self.client.acquire()
        try:
            restore(self.client.download(), self.allowed, self.root, self.config["deployment_id"])
            self.store = Store(self.root, self.config["deployment_id"], checkpoint=self.client.checkpoint)
            self.quota = SharedQuota(self.store, "collector", clock=self.clock)
            self.run_id = self.store.start_run(self.clock())
            self.store.set_meta("collector_current_run", self.run_id)
            self.budget = Budget({"max_seconds": 840, "max_requests": 60, "max_bytes": 64 * 1024 * 1024})
            # Control RPC is not /reader/api/0 traffic and has its own durable ledger.
            self.enabled = True
            self.reserve_api = lambda key: self.store.reserve(key, self.run_id, "oauth_control", self.clock(),
                                                             day_limit=200, cycle_limit=60)
            self.provider = AccessProvider(self.client, self, scope="read", clock=self.clock)
            self.held = True
            return self
        except BaseException:
            if self.store is not None:
                self.store.close()
            raise

    def access_token(self):
        require(self.held, "COLLECTOR_SHARED_LEASE")
        return self.provider()

    def api(self, path, *, params=None):
        require(self.held and isinstance(path, str), "COLLECTOR_SHARED_LEASE")
        url = sync.API + path
        if params:
            url += "?" + sync.urllib.parse.urlencode(params)
        require(api_zone(url, "GET") == "1", "COLLECTOR_READ_ONLY")
        return sync.request_json(url, headers={"Authorization": "Bearer " + self.access_token()},
                                 admission=self)

    def before_request(self, url, method):
        require(self.held and api_zone(url, method) == "1", "COLLECTOR_READ_ONLY")
        self.budget.request()
        self.quota.reserve_attempt("1")

    def observe(self, headers):
        self.quota.observe(headers)

    def open_request(self, request, *, timeout):
        return urllib.request.build_opener(NoRedirect).open(request, timeout=timeout)

    def read_json(self, response):
        raw = response.read(32 * 1024 * 1024 + 1)
        require(len(raw) <= 32 * 1024 * 1024, "SHARED_RESPONSE_SIZE")
        self.budget.add_bytes(len(raw))
        return parse_json(raw, limit=32 * 1024 * 1024)

    def __exit__(self, kind, error, tb):
        try:
            # Never release after an unknown checkpoint/OAuth response or hard interruption.
            if self.client.poisoned or self.store.poisoned or (kind and not issubclass(kind, Exception)):
                return False
            self.store.finish_run(self.run_id, self.clock(), {"mode": "COLLECTOR", "status":
                "FINISHED" if kind is None else "FAILED", "api_attempts": self.quota.attempts})
            self.client.release()
            self.held = False
        finally:
            self.held = False
            self.store.close()
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--allow-live", action="store_true")
    args, collector_args = parser.parse_known_args(argv)
    document = parse_json(read_file(Path(args.config)))
    # Validate configuration without credentials or live I/O unless explicitly enabled.
    settings(document)
    require(args.allow_live, "COLLECTOR_SHARED_LIVE_NOT_ALLOWED")
    if collector_args[:1] == ["--"]:
        collector_args = collector_args[1:]
    def halt(*_):
        raise CheckpointError("COLLECTOR_DEADLINE_OR_SIGNAL_HOLD")
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT)}
    try:
        for sig in previous:
            signal.signal(sig, halt)
        signal.alarm(MAX_SECONDS)
        with CollectorSession(document, token_provider=lambda: os.environ.get("SOURCE_DISCOVERY_STATE_TOKEN")) as session:
            sync.main(collector_args, services=session)
    finally:
        signal.alarm(0)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        print(json.dumps({"status": "HOLD", "reason": "SHARED_COLLECTOR_CONFIG_STATE_OR_API"}))
        raise SystemExit(2)
