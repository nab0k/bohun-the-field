"""A3SL-02 shadow registry: channel identity, aliases, disputes and item versions.

Offline prototype (EXP-034). Standard library only; writes to a local SQLite file.
IDs are derived from canonical keys, so the same inputs in any order give the same IDs.
Nothing here talks to the network, Supabase, Inoreader or GitHub.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RULES_VERSION = "a3sl-shadow-0.1"

# Only well-known tracking parameters are removed; everything else (lang, id, page...) is meaningful.
TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "_ga", "igshid", "yclid"}
TRACKING_PREFIXES = ("utm_",)
INTERNATIONAL = {"GLOBAL", "INTERNATIONAL", "INT", "WW", "WORLD", "WORLDWIDE"}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_url(url: str | None) -> str | None:
    """Canonical delivery/document address.

    - scheme and host lower-case, default ports and a leading "www." dropped, fragment dropped;
    - trailing slash dropped except for the root path; path case kept;
    - query kept and sorted, only tracking parameters removed.
    The original URL is always stored separately; this is a matching key, not a replacement.
    """
    if not url or not url.strip():
        return None
    raw = url.strip()
    if "://" not in raw:
        raw = "https://" + raw
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    if scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_PARAMS and not k.lower().startswith(TRACKING_PREFIXES)]
    query.sort()
    # http and https are treated as one delivery address; the original scheme stays in the record.
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def host_of(url: str | None) -> str | None:
    canon = canonical_url(url)
    return urlsplit(canon).hostname if canon else None


def normalize_country(value: str | None) -> str:
    """Country is never a rejection reason here: empty -> unknown, global -> international."""
    v = (value or "").strip()
    if not v:
        return "unknown"
    if v.upper() in INTERNATIONAL:
        return "international"
    return v.upper()


def stream_delivery_url(stream_id: str | None) -> str | None:
    """Inoreader feed stream IDs look like 'feed/<url>'; other stream kinds are not channels."""
    if stream_id and stream_id.startswith("feed/"):
        return stream_id[len("feed/"):]
    return None


def channel_id_for(canon: str) -> str:
    return "ch_" + sha256_text(canon)[:16]


def item_id_for(key: str) -> str:
    return "it_" + sha256_text(key)[:16]


@dataclass
class Record:
    """One row of one input snapshot, reduced to fields needed for reconciliation (no secrets, no text)."""
    source: str                  # input name, e.g. sd_csv / registry_csv / global_candidates / db_sources
    row_key: str                 # stable key inside that input
    name: str = ""
    homepage_url: str = ""
    delivery_url: str = ""
    stream_id: str = ""
    country: str = ""
    topic: str = ""
    aliases: list[tuple[str, str]] = field(default_factory=list)  # (alias_type, value) confirmed external IDs
    hints: dict = field(default_factory=dict)                     # unconfirmed hints (old host match, duplicate_of)
    is_provider: bool = False                                     # transport/provider record, not a channel

    @property
    def input_key(self) -> str:
        return f"{self.source}:{self.row_key}"

    def payload(self) -> dict:
        return {
            "source": self.source, "row_key": self.row_key, "name": self.name,
            "homepage_url": self.homepage_url, "delivery_url": self.delivery_url,
            "stream_id": self.stream_id, "country": self.country, "topic": self.topic,
            "aliases": sorted(self.aliases), "hints": self.hints, "is_provider": self.is_provider,
        }

    def raw_sha256(self) -> str:
        return sha256_text(json.dumps(self.payload(), sort_keys=True, ensure_ascii=False))


@dataclass
class Link:
    input_key: str
    status: str            # matched / disputed / unmatched / provider
    reason: str
    channel_id: str | None = None
    candidates: list[str] = field(default_factory=list)


def plan(records: list[Record]) -> tuple[dict[str, str], list[Link]]:
    """Pure, order-independent decision: which channels exist and how each record maps.

    Returns ({channel_id: canonical_url}, links sorted by input_key).
    """
    recs = sorted(records, key=lambda r: r.input_key)
    own: dict[str, str | None] = {}          # input_key -> canonical delivery url
    conflict: dict[str, str] = {}            # input_key -> reason
    for r in recs:
        if r.is_provider:
            continue
        d = canonical_url(r.delivery_url)
        s = canonical_url(stream_delivery_url(r.stream_id))
        if d and s and d != s:
            conflict[r.input_key] = "stream_url_conflict"
        own[r.input_key] = d or s

    # alias -> set of canonical urls seen with it; alias -> records carrying it
    alias_urls: dict[tuple[str, str], set[str]] = {}
    alias_recs: dict[tuple[str, str], set[str]] = {}
    for r in recs:
        if r.is_provider:
            continue
        keys = list(r.aliases)
        if r.stream_id:
            keys.append(("inoreader_stream", r.stream_id))
        for a in keys:
            alias_recs.setdefault(a, set()).add(r.input_key)
            if own.get(r.input_key):
                alias_urls.setdefault(a, set()).add(own[r.input_key])
    bad_alias = {a for a, urls in alias_urls.items() if len(urls) > 1}
    for a in bad_alias:
        for k in alias_recs[a]:
            conflict.setdefault(k, "alias_conflict:" + a[0])

    channels: dict[str, str] = {}
    for r in recs:
        k = r.input_key
        if not r.is_provider and own.get(k) and k not in conflict:
            channels[channel_id_for(own[k])] = own[k]
    host_index: dict[str, list[str]] = {}
    for cid, canon in channels.items():
        host_index.setdefault(urlsplit(canon).hostname, []).append(cid)

    links: list[Link] = []
    for r in recs:
        k = r.input_key
        if r.is_provider:
            links.append(Link(k, "provider", "transport_record"))
            continue
        if k in conflict:
            cands = sorted({channel_id_for(u) for a in (list(r.aliases) + ([("inoreader_stream", r.stream_id)] if r.stream_id else []))
                            for u in alias_urls.get(a, set())} | ({channel_id_for(own[k])} if own.get(k) else set()))
            links.append(Link(k, "disputed", conflict[k], None, cands))
            continue
        if own.get(k):
            links.append(Link(k, "matched", "delivery_url", channel_id_for(own[k])))
            continue
        via = sorted({channel_id_for(u) for a in r.aliases for u in alias_urls.get(a, set()) if a not in bad_alias})
        if len(via) == 1:
            links.append(Link(k, "matched", "alias", via[0]))
            continue
        host = host_of(r.homepage_url)
        same_host = sorted(host_index.get(host, [])) if host else []
        if same_host:
            # Old logic merged by host. Here a host match without a delivery address or ID is only a dispute.
            links.append(Link(k, "disputed", "host_only_match", None, same_host))
        else:
            links.append(Link(k, "unmatched", "no_delivery_url" if host else "no_url"))
    return channels, links


SCHEMA = """
CREATE TABLE IF NOT EXISTS run(run_id INTEGER PRIMARY KEY AUTOINCREMENT, rules_version TEXT NOT NULL,
  inputs_sha256 TEXT NOT NULL, counts_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS input_record(input_key TEXT NOT NULL, raw_sha256 TEXT NOT NULL, source TEXT NOT NULL,
  payload_json TEXT NOT NULL, first_run INTEGER NOT NULL, PRIMARY KEY(input_key, raw_sha256));
CREATE TABLE IF NOT EXISTS channel(channel_id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL UNIQUE, host TEXT NOT NULL,
  first_run INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS channel_alias(alias_type TEXT NOT NULL, alias_value TEXT NOT NULL, channel_id TEXT NOT NULL
  REFERENCES channel(channel_id), basis_input TEXT NOT NULL, PRIMARY KEY(alias_type, alias_value, channel_id));
CREATE TABLE IF NOT EXISTS link(input_key TEXT NOT NULL, raw_sha256 TEXT NOT NULL, status TEXT NOT NULL,
  reason TEXT NOT NULL, channel_id TEXT, candidates_json TEXT NOT NULL, rules_version TEXT NOT NULL,
  PRIMARY KEY(input_key, rules_version));
CREATE TABLE IF NOT EXISTS item(item_id TEXT PRIMARY KEY, canonical_key TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS item_alias(alias_type TEXT NOT NULL, alias_value TEXT NOT NULL, item_id TEXT NOT NULL
  REFERENCES item(item_id), PRIMARY KEY(alias_type, alias_value));
CREATE TABLE IF NOT EXISTS item_version(item_id TEXT NOT NULL REFERENCES item(item_id), version_no INTEGER NOT NULL,
  content_sha256 TEXT NOT NULL, PRIMARY KEY(item_id, version_no), UNIQUE(item_id, content_sha256));
CREATE TABLE IF NOT EXISTS item_observation(item_id TEXT NOT NULL, version_no INTEGER NOT NULL, channel_id TEXT NOT NULL,
  source_url TEXT NOT NULL, PRIMARY KEY(item_id, version_no, channel_id, source_url));
CREATE TABLE IF NOT EXISTS item_dispute(source_url TEXT NOT NULL, external_id TEXT NOT NULL, reason TEXT NOT NULL,
  candidates_json TEXT NOT NULL, PRIMARY KEY(source_url, external_id));
"""


class InjectedFailure(RuntimeError):
    pass


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def import_records(conn: sqlite3.Connection, records: list[Record], fail_after: int | None = None) -> dict:
    """Write one reconciliation run in a single transaction. Same inputs -> no new entity rows."""
    channels, links = plan(records)
    by_key = {r.input_key: r for r in records}
    inputs_sha = sha256_text("\n".join(sorted(f"{r.input_key}\t{r.raw_sha256()}" for r in records)))
    writes = 0

    def tick():
        nonlocal writes
        writes += 1
        if fail_after is not None and writes > fail_after:
            raise InjectedFailure(f"injected failure after {fail_after} writes")

    conn.execute("BEGIN")
    try:
        cur = conn.execute("INSERT INTO run(rules_version, inputs_sha256, counts_json) VALUES(?,?,?)",
                           (RULES_VERSION, inputs_sha, "{}"))
        run_id = cur.lastrowid
        tick()
        for r in sorted(records, key=lambda x: x.input_key):
            conn.execute("INSERT OR IGNORE INTO input_record VALUES(?,?,?,?,?)",
                         (r.input_key, r.raw_sha256(), r.source,
                          json.dumps(r.payload(), sort_keys=True, ensure_ascii=False), run_id))
            tick()
        for cid in sorted(channels):
            canon = channels[cid]
            conn.execute("INSERT OR IGNORE INTO channel VALUES(?,?,?,?)", (cid, canon, urlsplit(canon).hostname, run_id))
            tick()
        for ln in links:
            r = by_key[ln.input_key]
            conn.execute("INSERT OR REPLACE INTO link VALUES(?,?,?,?,?,?,?)",
                         (ln.input_key, r.raw_sha256(), ln.status, ln.reason, ln.channel_id,
                          json.dumps(ln.candidates), RULES_VERSION))
            tick()
            if ln.status == "matched":
                keys = list(r.aliases) + ([("inoreader_stream", r.stream_id)] if r.stream_id else [])
                keys.append(("input_row", ln.input_key))
                for at, av in sorted(keys):
                    conn.execute("INSERT OR IGNORE INTO channel_alias VALUES(?,?,?,?)", (at, av, ln.channel_id, ln.input_key))
                    tick()
        counts = summarize(conn)
        conn.execute("UPDATE run SET counts_json=? WHERE run_id=?", (json.dumps(counts, sort_keys=True), run_id))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"run_id": run_id, "inputs_sha256": inputs_sha, "counts": counts}


def summarize(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    status = dict(conn.execute(
        "SELECT status, count(*) FROM link WHERE rules_version=? GROUP BY status", (RULES_VERSION,)).fetchall())
    return {"input_records": q("SELECT count(*) FROM input_record"), "channels": q("SELECT count(*) FROM channel"),
            "aliases": q("SELECT count(*) FROM channel_alias"), "links": status,
            "items": q("SELECT count(*) FROM item"), "item_versions": q("SELECT count(*) FROM item_version")}


def state_fingerprint(conn: sqlite3.Connection) -> str:
    """Hash of all entity tables (run log excluded: it records each run by design)."""
    h = hashlib.sha256()
    for table in ("input_record", "channel", "channel_alias", "link", "item", "item_alias", "item_version",
                  "item_observation", "item_dispute"):
        rows = sorted(conn.execute(f"SELECT * FROM {table}").fetchall(), key=lambda r: json.dumps(r, ensure_ascii=False))
        h.update(table.encode())
        for row in rows:
            h.update(json.dumps(row, ensure_ascii=False).encode())
    return h.hexdigest()


def record_item(conn: sqlite3.Connection, channel_id: str, source_url: str, content_sha256: str,
                external_id: str | None = None, fail_after: int | None = None) -> dict:
    """Material identity: canonical document URL is the key, external ID is a confirmed alias.

    Same key + same hash -> same item and version (only a new observation if a new channel delivered it).
    Same key + new hash -> new version. URL and external ID pointing at different items -> dispute, nothing merged.
    A reprint at another URL is a separate item.
    """
    canon = canonical_url(source_url)
    if not canon:
        raise ValueError("source_url is not a valid http(s) URL")
    writes = 0

    def tick():
        nonlocal writes
        writes += 1
        if fail_after is not None and writes > fail_after:
            raise InjectedFailure(f"injected failure after {fail_after} writes")

    conn.execute("BEGIN")
    try:
        by_url = conn.execute("SELECT item_id FROM item WHERE canonical_key=?", (canon,)).fetchone()
        by_ext = None
        if external_id:
            by_ext = conn.execute("SELECT item_id FROM item_alias WHERE alias_type=? AND alias_value=?",
                                  ("external_id:" + channel_id, external_id)).fetchone()
        if by_url and by_ext and by_url[0] != by_ext[0]:
            conn.execute("INSERT OR IGNORE INTO item_dispute VALUES(?,?,?,?)",
                         (canon, external_id, "url_external_id_conflict", json.dumps(sorted([by_url[0], by_ext[0]]))))
            tick()
            conn.execute("COMMIT")
            return {"status": "disputed", "item_id": None, "version_no": None}
        if by_ext and not by_url:
            # Same publication under a changed URL is NOT merged automatically: domain/URL change needs confirmation.
            conn.execute("INSERT OR IGNORE INTO item_dispute VALUES(?,?,?,?)",
                         (canon, external_id, "external_id_new_url", json.dumps([by_ext[0]])))
            tick()
            conn.execute("COMMIT")
            return {"status": "disputed", "item_id": None, "version_no": None}
        item_id = by_url[0] if by_url else item_id_for(canon)
        if not by_url:
            conn.execute("INSERT INTO item VALUES(?,?)", (item_id, canon))
            tick()
        if external_id:
            conn.execute("INSERT OR IGNORE INTO item_alias VALUES(?,?,?)", ("external_id:" + channel_id, external_id, item_id))
            tick()
        row = conn.execute("SELECT version_no FROM item_version WHERE item_id=? AND content_sha256=?",
                           (item_id, content_sha256)).fetchone()
        if row:
            version_no, status = row[0], "same_version"
        else:
            version_no = conn.execute("SELECT coalesce(max(version_no),0)+1 FROM item_version WHERE item_id=?",
                                      (item_id,)).fetchone()[0]
            conn.execute("INSERT INTO item_version VALUES(?,?,?)", (item_id, version_no, content_sha256))
            tick()
            status = "new_item" if not by_url else "new_version"
        conn.execute("INSERT OR IGNORE INTO item_observation VALUES(?,?,?,?)", (item_id, version_no, channel_id, source_url))
        tick()
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"status": status, "item_id": item_id, "version_no": version_no}
