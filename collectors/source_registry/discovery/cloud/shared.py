"""Shared API admission under the existing coordinator lease and Store checkpoint."""
import json
import time
import uuid
from urllib.parse import urlsplit

from ..policy import Problem, require
from ..radars import API

READS = {"subscription/list", "tag/list", "user-info", "unread-count",
         "stream/items/contents", "stream/items/ids"}
WRITES = {"subscription/quickadd", "subscription/edit"}
KEY = "shared_inoreader_quota_v1"


def api_zone(url, method):
    require(isinstance(url, str) and url.startswith(API), "SHARED_API_ENDPOINT")
    parsed = urlsplit(url)
    require(not parsed.fragment and not parsed.username and not parsed.password, "SHARED_API_ENDPOINT")
    path = parsed.path[len("/reader/api/0/"):]
    if method == "GET" and (path in READS or path.startswith("stream/contents/")):
        return "1"
    if method == "POST" and path in WRITES:
        return "2"
    raise Problem("SHARED_API_METHOD")


class SharedQuota:
    """One ledger for collector and Discovery. Caller must hold the shared lease.

    Unknown/expired server windows admit one collector read probe, then require
    complete quota headers. Discovery preserves the collector reserve and has a
    separate daily ceiling. Uncertain attempts remain charged after restarts.
    """
    def __init__(self, store, consumer, *, clock=time.time, discovery_day_cap=12,
                 collector_reserve=20, max_attempts=60):
        require(consumer in ("collector", "discovery"), "SHARED_CONSUMER")
        require(type(discovery_day_cap) is int and 1 <= discovery_day_cap <= 20 and
                type(collector_reserve) is int and 20 <= collector_reserve <= 100 and
                type(max_attempts) is int and 1 <= max_attempts <= 100, "SHARED_LIMITS")
        self.store, self.consumer, self.clock = store, consumer, clock
        self.day_cap, self.reserve, self.max_attempts = discovery_day_cap, collector_reserve, max_attempts
        self.attempts = 0
        self.pending = None
        self.read()

    def read(self):
        raw = self.store.meta(KEY)
        if raw is None:
            return {"version": 1, "zones": {}, "daily": {}}
        try:
            value = json.loads(raw)
            require(value["version"] == 1 and isinstance(value["zones"], dict) and
                    isinstance(value["daily"], dict), "SHARED_QUOTA_STATE")
            for zone, row in value["zones"].items():
                require(zone in ("1", "2") and set(row) == {"limit", "charged", "reset_at", "verified"},
                        "SHARED_QUOTA_STATE")
                require(type(row["limit"]) is int and 1 <= row["limit"] <= 100 and
                        type(row["charged"]) is int and 0 <= row["charged"] <= 1_000_000 and
                        type(row["reset_at"]) is int and row["reset_at"] >= 0 and
                        type(row["verified"]) is bool, "SHARED_QUOTA_STATE")
            require(len(value["daily"]) <= 2 and all(isinstance(k, str) and type(v) is int and
                    0 <= v <= 20 for k, v in value["daily"].items()), "SHARED_QUOTA_STATE")
            return value
        except (ValueError, KeyError, TypeError):
            raise Problem("SHARED_QUOTA_STATE") from None

    def reserve_attempt(self, zone, *, attempt=None):
        require(zone in ("1", "2") and self.pending is None and
                self.attempts < self.max_attempts, "SHARED_ATTEMPT_GUARD")
        value = self.read()
        now = int(self.clock())
        day = time.strftime("%Y-%m-%d", time.gmtime(now))
        row = value["zones"].get(zone)
        if row is None or now >= row["reset_at"]:
            # A missing window is not assumed to have an unused daily allowance.
            # A prior unacknowledged probe cannot be repeated automatically.
            require(row is None or row["verified"], "SHARED_QUOTA_UNKNOWN")
            require(self.consumer == "collector" and zone == "1", "SHARED_COLLECTOR_PROBE_REQUIRED")
            row = {"limit": 100, "charged": 0, "reset_at": 0, "verified": False}
            value["zones"][zone] = row
        if not row["verified"]:
            require(self.consumer == "collector" and zone == "1" and row["charged"] == 0,
                    "SHARED_QUOTA_UNKNOWN")
        else:
            require(row["charged"] < row["limit"], "SHARED_QUOTA_EXHAUSTED")
            if self.consumer == "discovery":
                require(row["limit"] - row["charged"] > self.reserve, "SHARED_COLLECTOR_RESERVE")
        if self.consumer == "discovery":
            count = value["daily"].get(day, 0)
            require(count < self.day_cap, "SHARED_DISCOVERY_DAY_CAP")
            value["daily"] = {day: count + 1}
        row["charged"] += 1
        # Store.set_meta synchronously checkpoints before the caller sends HTTP.
        self.store.set_meta(KEY, json.dumps(value, sort_keys=True))
        self.pending = {"zone": zone, "attempt": attempt or uuid.uuid4().hex}
        self.attempts += 1

    def observe(self, headers):
        require(self.pending is not None, "SHARED_RESPONSE_WITHOUT_RESERVATION")
        values = {str(k).lower(): str(v) for k, v in headers.items()}
        names = ["x-reader-limits-reset-after"] + [
            "x-reader-zone" + zone + "-" + kind for zone in ("1", "2") for kind in ("limit", "usage")]
        require(all(values.get(k, "").isdigit() for k in names), "SHARED_QUOTA_HEADERS_REQUIRED")
        reset = int(values[names[0]])
        require(1 <= reset <= 86460, "SHARED_QUOTA_HEADERS")
        now = int(self.clock())
        value = self.read()
        for zone in ("1", "2"):
            limit, used = (int(values["x-reader-zone" + zone + "-" + kind]) for kind in ("limit", "usage"))
            require(1 <= limit <= 1_000_000 and 0 <= used <= 1_000_000, "SHARED_QUOTA_HEADERS")
            row = value["zones"].get(zone)
            if row is not None and row["verified"] and now < row["reset_at"]:
                require(abs(row["reset_at"] - (now + reset)) <= 120, "SHARED_QUOTA_WINDOW_CHANGED")
                charged = max(row["charged"], used)
                end = max(row["reset_at"], now + reset)
                cap = min(row["limit"], limit, 100)
            else:
                charged = max(row["charged"], used) if row and not row["verified"] else used
                end, cap = now + reset, min(limit, 100)
            value["zones"][zone] = {"limit": cap, "charged": charged, "reset_at": end, "verified": True}
        self.store.set_meta(KEY, json.dumps(value, sort_keys=True))
        self.pending = None


class SharedHTTP:
    """Reuse the existing HTTP transport and guards; charge every API attempt."""
    def __init__(self, http, quota):
        self.http, self.quota = http, quota
        self.budget, self.enabled = http.budget, http.enabled
        # OAuth control RPC has its separate existing durable reservation. It is
        # not /reader/api/0 traffic and cannot seed an API quota window.
        self.reserve_api = http.reserve_api
        self.zone = None
        def before(key):
            require(self.zone is not None, "SHARED_API_CONTEXT")
            if self.reserve_api is not None:
                self.reserve_api(key)
            self.quota.reserve_attempt(self.zone, attempt=key)
        self.http.reserve_api = before

    def request(self, url, **kwargs):
        api = kwargs.get("api", False)
        require(not url.startswith(API) or api is True, "SHARED_API_ADMISSION_REQUIRED")
        if not api:
            return self.http.request(url, **kwargs)
        require(self.zone is None, "SHARED_API_REENTRANCY")
        self.zone = api_zone(url, kwargs.get("method", "GET"))
        try:
            response = self.http.request(url, **kwargs)
            self.quota.observe(response.headers)
            return response
        finally:
            self.zone = None
