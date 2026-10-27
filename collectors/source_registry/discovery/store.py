"""Single-host operational journal. No production source master or DB connection."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import uuid

from .policy import Problem, digest, identity_url, parse_json, read_file, require, stamp


SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE runs (id TEXT PRIMARY KEY, started REAL, finished REAL, receipt TEXT);
CREATE TABLE observations (id TEXT PRIMARY KEY, provider TEXT, query_id TEXT, payload TEXT, seen REAL, last_seen REAL);
CREATE TABLE candidates (id TEXT PRIMARY KEY, url TEXT UNIQUE, payload TEXT, state TEXT,
                         attempts INTEGER DEFAULT 0, retry_at REAL DEFAULT 0);
CREATE TABLE provider_cursors (id TEXT PRIMARY KEY, payload TEXT);
CREATE TABLE channel_profiles (id TEXT PRIMARY KEY, endpoint TEXT UNIQUE, payload TEXT);
CREATE TABLE validation_receipts (id TEXT PRIMARY KEY, candidate_id TEXT, payload TEXT, seen REAL);
CREATE TABLE connection_operations (id TEXT PRIMARY KEY, profile_id TEXT, run_id TEXT,
                                    state TEXT, payload TEXT, created REAL);
CREATE TABLE attempt_ledger (id TEXT PRIMARY KEY, operation_id TEXT, run_id TEXT,
                            kind TEXT, day TEXT, seen REAL);
CREATE TABLE delivery_checks (id TEXT PRIMARY KEY, profile_id TEXT, payload TEXT, seen REAL);
CREATE TABLE review_events (id TEXT PRIMARY KEY, kind TEXT, ref TEXT, payload TEXT, seen REAL);
"""


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


class CheckpointError(BaseException):
    """Fatal: committed local state may differ from its durable cloud copy."""


class Store:
    def __init__(self, root, deployment, *, bootstrap=False, checkpoint=None):
        require(checkpoint is None or callable(checkpoint), "CHECKPOINT_CALLBACK")
        self.checkpoint, self.poisoned = checkpoint, False
        self.root = Path(root)
        require(self.root.is_absolute() and ".." not in self.root.parts and
                not any(p.is_symlink() for p in (self.root, *self.root.parents)), "STATE_PATH")
        require(deployment and len(deployment) <= 80 and
                all(c.isalnum() or c in "-_" for c in deployment), "DEPLOYMENT_ID")
        self.deployment = deployment
        self.marker = self.root.parent / ("." + deployment + ".deployment.json")
        self.dbpath = self.root / "state.sqlite"
        self.lock = None
        self.db = None
        if bootstrap:
            require(not self.dbpath.exists() and not self.marker.exists(), "BOOTSTRAP_ALREADY_EXISTS")
            self.root.mkdir(mode=0o700, parents=False, exist_ok=True)
        require(self.root.is_dir(), "STATE_MISSING_HOLD")
        lockpath = self.root / "worker.lock"
        require(not lockpath.is_symlink(), "STATE_SYMLINK")
        self.lock = os.open(lockpath, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if bootstrap:
                # Exclusive marker survives loss of the SQLite file; no silent fresh start.
                with self.marker.open("x", encoding="utf-8") as out:
                    json.dump({"deployment": deployment, "format": 1}, out)
                    out.flush()
                    os.fsync(out.fileno())
                with self.dbpath.open("xb"):
                    pass
                os.chmod(self.dbpath, 0o600)
                self.db = sqlite3.connect(self.dbpath)
                self.db.executescript(SCHEMA)
                with self.db:
                    self.db.execute("INSERT INTO metadata VALUES ('deployment', ?)", (deployment,))
                    self.db.execute("INSERT INTO metadata VALUES ('schema', '1')")
            else:
                require(self.dbpath.is_file() and not self.dbpath.is_symlink() and
                        self.marker.is_file() and not self.marker.is_symlink(), "STATE_MISSING_HOLD")
                marker = parse_json(read_file(self.marker, limit=4096))
                require(isinstance(marker, dict) and marker.get("deployment") == deployment and
                        marker.get("format") == 1, "MARKER_MISMATCH")
                self.db = sqlite3.connect(self.dbpath.as_uri() + "?mode=rw", uri=True)
            self.db.row_factory = sqlite3.Row
            self.db.execute("PRAGMA synchronous=FULL")
            require(self.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "STATE_CORRUPT")
            require(self.meta("deployment") == deployment and self.meta("schema") == "1", "STATE_IDENTITY")
        except sqlite3.Error:
            self.close()
            raise Problem("STATE_CORRUPT_OR_SCHEMA") from None
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextlib.contextmanager
    def transaction(self):
        if self.poisoned:
            raise CheckpointError("STATE_CHECKPOINT_HOLD")
        with self.db:
            yield
        self.sync_checkpoint()

    def sync_checkpoint(self):
        if self.poisoned:
            raise CheckpointError("STATE_CHECKPOINT_HOLD")
        if self.checkpoint is not None:
            try:
                require(self.checkpoint(self) is None, "CHECKPOINT_ACK_REQUIRED")
            except BaseException:
                self.poisoned = True
                raise CheckpointError("STATE_CHECKPOINT_HOLD") from None

    def meta(self, key, default=None):
        row = self.db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key, value):
        with self.transaction():
            self.db.execute("INSERT OR REPLACE INTO metadata VALUES (?, ?)", (key, str(value)))

    def start_run(self, now):
        run = uuid.uuid4().hex
        with self.transaction():
            self.db.execute("INSERT INTO runs VALUES (?, ?, NULL, NULL)", (run, now))
        return run

    def finish_run(self, run, now, receipt):
        with self.transaction():
            self.db.execute("UPDATE runs SET finished=?, receipt=? WHERE id=?", (now, encoded(receipt), run))

    def cursor(self, provider):
        row = self.db.execute("SELECT payload FROM provider_cursors WHERE id=?", (provider,)).fetchone()
        return json.loads(row[0]) if row else {}

    def ingest(self, provider, query_id, observations, cursor, now):
        require(len(observations) <= 100, "OBSERVATION_CAP")
        inserted = 0
        processing = {"producer": self.meta("producer", "UNKNOWN"),
                      "policy_sha256": self.meta("policy_sha256", "UNKNOWN"),
                      "run_id": self.meta("current_run", "UNKNOWN")}
        with self.transaction():
            for obs in observations:
                require(set(obs) <= {"item_id", "article_url", "publisher_url", "feed_url", "title", "method", "error", "source_page"},
                        "OBSERVATION_FIELDS")
                require(isinstance(obs.get("item_id"), str), "OBSERVATION_ID")
                for key in ("article_url", "publisher_url", "feed_url", "source_page"):
                    if obs.get(key):
                        identity_url(obs[key])
                oid = digest([provider, query_id, obs["item_id"]])
                cur = self.db.execute("INSERT OR IGNORE INTO observations VALUES (?, ?, ?, ?, ?, ?)",
                                      (oid, provider, query_id, encoded({**obs, "processing": processing}), now, now))
                inserted += cur.rowcount
                if not cur.rowcount:
                    self.db.execute("UPDATE observations SET last_seen=? WHERE id=?", (now, oid))
                target = obs.get("feed_url") or obs.get("publisher_url")
                if target:
                    target = identity_url(target)
                    cid = digest(target)
                    payload = {"url": target, "observation_ids": [oid], "discovered_at": stamp(now), "last_seen_at": stamp(now)}
                    row = self.db.execute("SELECT payload FROM candidates WHERE id=?", (cid,)).fetchone()
                    if row:
                        payload = json.loads(row[0])
                        payload["last_seen_at"] = stamp(now)
                        payload["publisher_hints"] = list(dict.fromkeys(payload.get("publisher_hints", []) +
                            ([obs["publisher_url"]] if obs.get("publisher_url") else [])))[:20]
                        payload["observation_ids"] = list(dict.fromkeys(payload["observation_ids"] + [oid]))[-100:]
                        self.db.execute("UPDATE candidates SET payload=? WHERE id=?", (encoded(payload), cid))
                    else:
                        payload["publisher_hints"] = [obs["publisher_url"]] if obs.get("publisher_url") else []
                        self.db.execute("INSERT INTO candidates (id,url,payload,state) VALUES (?,?,?,'DISCOVERED')",
                                        (cid, target, encoded(payload)))
                elif cur.rowcount:
                    self._event("REVIEW", oid, {"reason": obs.get("error", "PUBLISHER_UNKNOWN")}, now)
            # Cursor and all observations commit together, including an empty page.
            self.db.execute("INSERT OR REPLACE INTO provider_cursors VALUES (?,?)",
                            (provider, encoded({**cursor, "processing": processing})))
        return inserted

    def due(self, now, limit):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM candidates WHERE retry_at<=? AND state IN ('DISCOVERED','FAILED','REVIEW') "
            "ORDER BY retry_at,id LIMIT ?", (now, limit))]

    def validate(self, cid, profiles, now):
        with self.transaction():
            for feed in profiles:
                fid = digest([identity_url(feed["feed_url"]), feed.get("language", ""),
                              feed.get("section", ""), feed.get("access", "UNKNOWN")])
                receipt = {**feed, "profile_id": fid, "candidate_id": cid, "validated_at": now}
                row = self.db.execute("SELECT id,payload FROM channel_profiles WHERE endpoint=?", (feed["feed_url"],)).fetchone()
                if row and row[0] != fid:
                    self._event("REVIEW", cid, {"reason": "PROFILE_IDENTITY_CONFLICT", "existing": row[0]}, now)
                    continue
                if row:
                    previous = json.loads(row[1])
                    for key in ("connection", "delivery", "lifecycle"):
                        if key in previous:
                            receipt[key] = previous[key]
                urls = {e["url"] for e in receipt.get("entries", [])}
                for other in self.db.execute("SELECT payload FROM channel_profiles WHERE endpoint<>?", (feed["feed_url"],)):
                    known = json.loads(other[0])
                    known_urls = {e["url"] for e in known.get("entries", [])}
                    if len(urls) >= 10 and urls == known_urls and receipt.get("language") == known.get("language"):
                        receipt["assessment"] = {**receipt["assessment"], "decision": "REVIEW",
                            "rule_ids": sorted(set(receipt["assessment"]["rule_ids"] + ["gate:duplicate-observed-window"]))}
                        self._event("REVIEW", fid, {"reason": "DUPLICATE_WINDOW_REVIEW", "other_profile": known["profile_id"]}, now)
                rid = digest([cid, fid, now, receipt])
                self.db.execute("INSERT OR IGNORE INTO validation_receipts VALUES (?,?,?,?)",
                                (rid, cid, encoded(receipt), now))
                self.db.execute("INSERT OR REPLACE INTO channel_profiles VALUES (?,?,?)",
                                (fid, feed["feed_url"], encoded(receipt)))
            self.db.execute("UPDATE candidates SET state='VALIDATED',attempts=attempts+1 WHERE id=?", (cid,))

    def fail(self, cid, reason, now, *, review=False):
        with self.transaction():
            row = self.db.execute("SELECT attempts FROM candidates WHERE id=?", (cid,)).fetchone()
            n = row[0] if row else 0
            delay = 86400 if review else min(86400, 3600 * 2 ** min(n, 5))
            self.db.execute("UPDATE candidates SET state=?,attempts=attempts+1,retry_at=? WHERE id=?",
                            ("REVIEW" if review else "FAILED", now + delay, cid))
            self._event("REVIEW" if review else "VALIDATION_FAILED", cid, {"reason": reason}, now)

    def profiles(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM channel_profiles ORDER BY id")]

    def put_profile(self, feed):
        with self.transaction():
            self.db.execute("UPDATE channel_profiles SET payload=? WHERE id=?", (encoded(feed), feed["profile_id"]))

    def _event(self, kind, ref, payload, now):
        eid = digest([kind, ref, payload, now] if kind == "HEALTH_CHANGE" else [kind, ref, payload])
        self.db.execute("INSERT OR IGNORE INTO review_events VALUES (?,?,?,?,?)",
                        (eid, kind, ref, encoded(payload), now))

    def event(self, kind, ref, payload, now):
        with self.transaction():
            self._event(kind, ref, payload, now)

    def reserve(self, operation, run, kind, now, *, day_limit, cycle_limit):
        day = stamp(now)[:10]
        key = digest([operation, kind])
        # One durable reservation per operation/kind; no write retry, even after restart.
        with self.transaction():
            self.db.execute("BEGIN IMMEDIATE")
            require(not self.db.execute("SELECT 1 FROM attempt_ledger WHERE id=?", (key,)).fetchone(),
                    "ATTEMPT_ALREADY_RESERVED")
            latest = self.db.execute("SELECT max(seen) FROM attempt_ledger").fetchone()[0]
            require(latest is None or now + 60 >= latest, "CLOCK_REVERSED")
            count = self.db.execute("SELECT count(*) FROM attempt_ledger WHERE kind=? AND day=?", (kind, day)).fetchone()[0]
            cycle = self.db.execute("SELECT count(*) FROM attempt_ledger WHERE kind=? AND run_id=?", (kind, run)).fetchone()[0]
            require(count < day_limit and cycle < cycle_limit, "QUOTA_BLOCKED")
            self.db.execute("INSERT INTO attempt_ledger VALUES (?,?,?,?,?,?)", (key, operation, run, kind, day, now))

    def begin_operation(self, profile, run, now):
        require(not self.unresolved(), "UNRESOLVED_WRITE_HOLD")
        op = uuid.uuid4().hex
        with self.transaction():
            self.db.execute("INSERT INTO connection_operations VALUES (?,?,?,'PREPARED',?,?)",
                            (op, profile["profile_id"], run, encoded({"feed_url": profile["feed_url"],
                             "group": profile["assessment"]["group"], "policy_sha256": profile.get("policy_sha256"),
                             "producer": profile.get("producer", "UNKNOWN")}), now))
        return op

    def operation_state(self, op, state, payload=None):
        with self.transaction():
            if payload is None:
                self.db.execute("UPDATE connection_operations SET state=? WHERE id=?", (state, op))
            else:
                self.db.execute("UPDATE connection_operations SET state=?,payload=? WHERE id=?", (state, encoded(payload), op))

    def unresolved(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM connection_operations WHERE state NOT IN "
                                                "('CONFIRMED','CANCELLED','RECONCILED') ORDER BY created")]

    def has_attempt(self, op):
        return bool(self.db.execute("SELECT 1 FROM attempt_ledger WHERE operation_id=? AND kind='quickadd'", (op,)).fetchone())

    def record_delivery(self, fid, receipt, now):
        with self.transaction():
            self.db.execute("INSERT OR IGNORE INTO delivery_checks VALUES (?,?,?,?)",
                            (digest([fid, receipt]), fid, encoded(receipt), now))

    def summary(self):
        tables = ("observations", "candidates", "channel_profiles", "connection_operations", "attempt_ledger", "review_events")
        return {name: self.db.execute("SELECT count(*) FROM " + name).fetchone()[0] for name in tables}
