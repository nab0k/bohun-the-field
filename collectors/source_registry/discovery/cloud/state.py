"""Authenticated private checkpoints. Every uncertain response poisons this client."""
import hashlib
import json
import re
import secrets
import time

from ..policy import parse_json
from ..radars import Budget, HTTP
from ..store import CheckpointError

MAX_SNAPSHOT = 2 * 1024 * 1024
MAX_CALLS = 300
MAX_TRANSFER = 64 * 1024 * 1024
MAX_SECONDS = 900
ENDPOINT = re.compile(r"https://bohun-source-discovery-state\.[a-z0-9-]+\.workers\.dev\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


class CloudHold(CheckpointError):
    """Do not retry, release a lease, or continue a source write after this error."""


def check(condition):
    if not condition:
        raise CloudHold("CLOUD_STATE_HOLD")


def wire(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class StateClient:
    def __init__(self, endpoint, deployment, code_hash, policy_hash, token_provider, *,
                 enabled=False, transport=None, monotonic=time.monotonic):
        check(isinstance(endpoint, str) and ENDPOINT.fullmatch(endpoint))
        check(isinstance(deployment, str) and NAME.fullmatch(deployment))
        check(all(isinstance(x, str) and HEX.fullmatch(x) for x in (code_hash, policy_hash)))
        check(callable(token_provider) and type(enabled) is bool)
        self.endpoint, self.deployment = endpoint, deployment
        self.code_hash, self.policy_hash = code_hash, policy_hash
        self.token_provider, self.enabled, self.clock = token_provider, enabled, monotonic
        self.deadline = monotonic() + MAX_SECONDS
        self.http = transport or HTTP(Budget({"max_seconds": MAX_SECONDS,
            "max_requests": MAX_CALLS, "max_bytes": MAX_TRANSFER}, monotonic=monotonic), enabled=enabled)
        self.owner = secrets.token_hex(32)
        self.generation = self.revision = None
        self.manifest = None
        self.held = self.poisoned = False
        self.calls = self.transferred = 0
        self._token = None

    def _request(self, path, *, raw=None, data=None, lease=False, limit=16384, extra=None):
        try:
            check(not self.poisoned and self.enabled and self.clock() < self.deadline)
            check(self.calls < MAX_CALLS and (not lease or self.held))
            body = raw if raw is not None else wire(data or {})
            check(isinstance(body, bytes) and len(body) <= MAX_SNAPSHOT)
            check(self.transferred + len(body) <= MAX_TRANSFER)
            self.calls += 1
            self.transferred += len(body)
            if self._token is None:
                value = self.token_provider()
                check(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,256}", value))
                self._token = value
            headers = {"Authorization": "Bearer " + self._token,
                "Content-Type": "application/octet-stream" if raw is not None else "application/json",
                "X-Deployment": self.deployment, "X-Code-Sha256": self.code_hash,
                "X-Policy-Sha256": self.policy_hash}
            if lease:
                headers.update({"X-Owner": self.owner, "X-Generation": str(self.generation),
                                "X-Revision": str(self.revision)})
            headers.update(extra or {})
            response = self.http.request(self.endpoint + path, method="POST", headers=headers,
                                         body=body, limit=limit)
            self.transferred += len(response.body)
            check(self.transferred <= MAX_TRANSFER and self.clock() < self.deadline)
            check(response.status == 200 and response.url == self.endpoint + path)
            return response.body
        except BaseException:
            self.poisoned = True
            raise CloudHold("CLOUD_STATE_HOLD") from None

    def _identity(self, data):
        check(isinstance(data, dict) and data.get("version") == 1 and
              data.get("deployment") == self.deployment and
              data.get("code_sha256") == self.code_hash and
              data.get("policy_sha256") == self.policy_hash)

    def _json(self, raw):
        try:
            data = parse_json(raw, limit=16384)
            self._identity(data)
            return data
        except BaseException:
            self.poisoned = True
            raise CloudHold("CLOUD_STATE_HOLD") from None

    def bootstrap(self, snapshot):
        try:
            check(not self.held and isinstance(snapshot, bytes) and 0 < len(snapshot) <= MAX_SNAPSHOT)
            sha = hashlib.sha256(snapshot).hexdigest()
            data = self._json(self._request("/v1/bootstrap", raw=snapshot,
                                           extra={"X-Snapshot-Sha256": sha}))
            check(data.get("status") == "BOOTSTRAPPED" and data.get("revision") == 0 and
                  data.get("sha256") == sha)
        except BaseException:
            self.poisoned = True
            raise CloudHold("CLOUD_STATE_HOLD") from None

    def acquire(self):
        try:
            check(not self.held)
            data = self._json(self._request("/v1/acquire", data={"owner": self.owner}))
            check(data.get("owner") == self.owner and type(data.get("generation")) is int and
                  data["generation"] > 0 and type(data.get("revision")) is int and data["revision"] >= 0)
            manifest = data.get("manifest")
            self._identity(manifest)
            check(manifest.get("schema") == 1 and type(manifest.get("size")) is int and
                  0 < manifest["size"] <= MAX_SNAPSHOT and
                  isinstance(manifest.get("sha256"), str) and HEX.fullmatch(manifest["sha256"]))
            self.generation, self.revision, self.manifest = data["generation"], data["revision"], manifest
            self.held = True
        except BaseException:
            self.poisoned = True
            raise CloudHold("CLOUD_STATE_HOLD") from None

    def download(self):
        try:
            raw = self._request("/v1/download", lease=True, limit=MAX_SNAPSHOT)
            check(self.manifest is not None and len(raw) == self.manifest["size"] and
                  hashlib.sha256(raw).hexdigest() == self.manifest["sha256"] and
                  raw.startswith(b"SQLite format 3\x00"))
            return raw
        except BaseException:
            self.poisoned = True
            raise CloudHold("CLOUD_STATE_HOLD") from None

    def checkpoint(self, store):
        try:
            check(not self.poisoned and self.held and not store.db.in_transaction and
                  store.deployment == self.deployment)
            raw = store.db.serialize()
            check(0 < len(raw) <= MAX_SNAPSHOT)
            sha = hashlib.sha256(raw).hexdigest()
            data = self._json(self._request("/v1/checkpoint", raw=raw, lease=True,
                                           extra={"X-Snapshot-Sha256": sha}))
            check(data.get("status") == "CHECKPOINTED" and data.get("owner") == self.owner and
                  data.get("generation") == self.generation and
                  type(data.get("revision")) is int and data["revision"] == self.revision + 1 and
                  data.get("sha256") == sha)
            self.revision = data["revision"]
        except BaseException:
            self.poisoned = True
            raise CloudHold("CLOUD_STATE_HOLD") from None

    def release(self):
        try:
            data = self._json(self._request("/v1/release", lease=True))
            check(data.get("status") == "RELEASED" and data.get("revision") == self.revision)
            self.held = False
        except BaseException:
            self.poisoned = True
            raise CloudHold("CLOUD_STATE_HOLD") from None

    def oauth_bootstrap(self):
        """Explicit one-time credential seed; no secrets are supplied by Python."""
        try:
            data = self._json(self._request("/v1/oauth-bootstrap", lease=True))
            check(data.get("status") == "OAUTH_BOOTSTRAPPED" and data.get("credential_generation") == 0)
            self._oauth_fence(data)
        except BaseException:
            self.poisoned = True
            raise CloudHold("CLOUD_OAUTH_HOLD") from None

    def _oauth_fence(self, data):
        check(data.get("owner") == self.owner and type(data.get("generation")) is int and
              data["generation"] == self.generation and type(data.get("revision")) is int and
              data["revision"] == self.revision)

    def oauth_access(self, scope):
        try:
            check(scope in ("read", "read write"))
            data = self._json(self._request("/v1/oauth-access", data={"scope": scope}, lease=True))
            check(set(data) == {"version", "deployment", "code_sha256", "policy_sha256", "status",
                "owner", "generation", "revision", "credential_generation", "access_token", "expires_at", "scope"})
            self._oauth_fence(data)
            check(data.get("status") == "OAUTH_ACCESS" and type(data.get("credential_generation")) is int and
                  data["credential_generation"] > 0)
            return data
        except BaseException:
            self.poisoned = True
            raise CloudHold("CLOUD_OAUTH_HOLD") from None
