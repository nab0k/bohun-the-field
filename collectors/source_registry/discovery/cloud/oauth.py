"""Lease-scoped access in memory. Refresh credentials stay in the coordinator."""
import re
import time
import uuid

from .state import CloudHold

TOKEN = re.compile(r"[\x21-\x7e]{1,4096}\Z")
MAX_TTL = 31 * 86400
# Conservatively reserve the bounded coordinator/upstream bodies before any I/O.
EXCHANGE_BYTES = 128 * 1024


class AccessProvider:
    def __init__(self, client, http, *, scope="read", clock=time.time):
        self.client, self.http, self.scope, self.clock = client, http, scope, clock
        self.cached = None
        self.lease = (client.owner, client.generation)

    def __call__(self):
        try:
            if (self.scope not in ("read", "read write") or not self.client.held or
                    self.client.poisoned or (self.client.owner, self.client.generation) != self.lease or
                    not self.http.enabled):
                raise CloudHold("CLOUD_OAUTH_HOLD")
            now = self.clock()
            if (self.cached is not None and self.cached["expires_at"] > now + 60 and
                    set(self.scope.split()).issubset(self.cached["scope"].split())):
                return self.cached["access_token"]
        except BaseException:
            self.cached = None
            self.client.poisoned = True
            raise CloudHold("CLOUD_OAUTH_HOLD") from None
        # Known quota failures before RPC are safe to report; they must not strand a lease.
        self.http.budget.request()
        self.http.budget.add_bytes(EXCHANGE_BYTES)
        if self.http.reserve_api is None:
            raise CloudHold("CLOUD_OAUTH_HOLD")
        self.http.reserve_api(uuid.uuid4().hex)  # durable ACK before RPC/upstream POST
        try:
            data = self.client.oauth_access(self.scope)
            now = self.clock()
            if (not isinstance(data.get("access_token"), str) or not TOKEN.fullmatch(data["access_token"]) or
                    type(data.get("expires_at")) is not int or
                    not now + 60 < data["expires_at"] <= now + MAX_TTL or
                    data.get("scope") not in ("read", "read write") or
                    not set(self.scope.split()).issubset(data["scope"].split()) or
                    "refresh_token" in data):
                raise CloudHold("CLOUD_OAUTH_HOLD")
            self.cached = {k: data[k] for k in ("access_token", "expires_at", "scope")}
            return data["access_token"]
        except BaseException:
            self.cached = None
            self.client.poisoned = True
            raise CloudHold("CLOUD_OAUTH_HOLD") from None
