"""Storage port for the companies pipeline (EXP-012).

SqliteStore mirrors db/companies/001_companies_schema.sql (PostgreSQL) so that the whole pipeline
runs and is tested offline. A PostgreSQL store for Supabase is a separate, not yet authorized step
(TODO v1 T08): writes to Supabase are not allowed at G3.
PgStore (pg_store.py) reuses the same methods through the `_q` hook (T08, 18.10.2026).
"""
import datetime as dt
import hashlib
import json
import secrets
import sqlite3

from .normalize import name_norm

SCHEMA = """
CREATE TABLE IF NOT EXISTS company (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, name_norm TEXT NOT NULL, country TEXT, city TEXT,
  website TEXT, supply_role TEXT, defence_relation TEXT, flags TEXT NOT NULL DEFAULT '[]',
  layer TEXT, status TEXT NOT NULL DEFAULT 'candidate', excluded_reason TEXT, ownership_check TEXT,
  notion_page_id TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  CHECK (NOT (country = 'UA' AND city IS NOT NULL)),
  CHECK ((status = 'excluded') = (excluded_reason IS NOT NULL)),
  CHECK (status IN ('candidate','card','dossier','excluded')));
CREATE INDEX IF NOT EXISTS company_name_country ON company(name_norm, country);
CREATE TABLE IF NOT EXISTS company_identifier (
  scheme TEXT NOT NULL, value TEXT NOT NULL, company_id TEXT NOT NULL REFERENCES company(id),
  source TEXT NOT NULL, observed_at TEXT NOT NULL, PRIMARY KEY (scheme, value));
CREATE TABLE IF NOT EXISTS company_alias (
  company_id TEXT NOT NULL REFERENCES company(id), alias TEXT NOT NULL, alias_norm TEXT NOT NULL,
  source TEXT NOT NULL, PRIMARY KEY (company_id, alias_norm));
CREATE INDEX IF NOT EXISTS company_alias_norm ON company_alias(alias_norm);
CREATE TABLE IF NOT EXISTS observation (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, source_key TEXT NOT NULL,
  content_hash TEXT NOT NULL, fetched_at TEXT NOT NULL, payload TEXT NOT NULL,
  UNIQUE (source, source_key, content_hash));
CREATE TABLE IF NOT EXISTS signal (
  id INTEGER PRIMARY KEY AUTOINCREMENT, company_id TEXT NOT NULL REFERENCES company(id),
  type TEXT NOT NULL, layer TEXT NOT NULL, counterpart TEXT, signal_date TEXT, url TEXT,
  observation_id INTEGER REFERENCES observation(id), UNIQUE (company_id, type, observation_id));
CREATE TABLE IF NOT EXISTS company_category (
  company_id TEXT NOT NULL REFERENCES company(id), axis TEXT NOT NULL, value TEXT NOT NULL,
  basis TEXT NOT NULL, PRIMARY KEY (company_id, axis, value));
CREATE TABLE IF NOT EXISTS company_relation (
  from_id TEXT NOT NULL REFERENCES company(id), to_id TEXT NOT NULL REFERENCES company(id),
  relation TEXT NOT NULL, source TEXT NOT NULL, PRIMARY KEY (from_id, to_id, relation));
CREATE TABLE IF NOT EXISTS company_ownership_link (
  company_id TEXT NOT NULL REFERENCES company(id), owner_country TEXT NOT NULL, owner_name TEXT,
  share_text TEXT, link_type TEXT NOT NULL, source TEXT NOT NULL, observed_at TEXT NOT NULL,
  PRIMARY KEY (company_id, owner_country, link_type, source));
CREATE TABLE IF NOT EXISTS match_candidate (
  observation_id INTEGER NOT NULL REFERENCES observation(id), company_id TEXT NOT NULL REFERENCES company(id),
  reason TEXT NOT NULL, mention_name TEXT NOT NULL, PRIMARY KEY (observation_id, company_id));
CREATE TABLE IF NOT EXISTS source_state (
  source TEXT PRIMARY KEY, cursor TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0,
  disabled INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS discovery_candidate (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observation_id INTEGER NOT NULL REFERENCES observation(id),
  name TEXT NOT NULL, name_norm TEXT NOT NULL, reported_country TEXT,
  website TEXT, role TEXT NOT NULL DEFAULT 'lead',
  status TEXT NOT NULL CHECK (status IN ('discovered','candidate_existing','hold','rejected','promotion_ready')),
  matched_company_id TEXT REFERENCES company(id), match_reason TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE (observation_id,name_norm));
CREATE INDEX IF NOT EXISTS dc_status ON discovery_candidate(status);
CREATE TABLE IF NOT EXISTS schema_migration (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, started_at TEXT NOT NULL,
  finished_at TEXT, cursor_in TEXT, cursor_out TEXT, fetched INTEGER NOT NULL DEFAULT 0,
  new_observations INTEGER NOT NULL DEFAULT 0, new_companies INTEGER NOT NULL DEFAULT 0,
  matched INTEGER NOT NULL DEFAULT 0, candidates INTEGER NOT NULL DEFAULT 0,
  skipped INTEGER NOT NULL DEFAULT 0, discoveries INTEGER NOT NULL DEFAULT 0,
  candidate_existing INTEGER NOT NULL DEFAULT 0, holds INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL, error TEXT);
"""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def content_hash(payload):
    canon = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _iso_date(value):
    """'YYYY-MM-DD' or None: a malformed source date must not break the run (column is DATE in Postgres)."""
    value = str(value or "")[:10]
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def new_company_id():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"co_{stamp}{secrets.token_hex(5)}"


class SqliteStore:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self._q("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    def _q(self, sql, params=()):
        return self.db.execute(sql, params)

    def close(self):
        self.db.close()

    # ---------- observations ----------
    def add_observation(self, source, source_key, payload):
        """Return (observation_id, is_new). Same content twice is a no-op (AC1)."""
        h = content_hash(payload)
        row = self._q("SELECT id FROM observation WHERE source=? AND source_key=? AND content_hash=?",
                              (source, source_key, h)).fetchone()
        if row:
            return row["id"], False
        row = self._q(
            """INSERT INTO observation(source, source_key, content_hash, fetched_at, payload) VALUES (?,?,?,?,?)
               RETURNING id""",
            (source, source_key, h, now(), json.dumps(payload, ensure_ascii=False, sort_keys=True))).fetchone()
        return row["id"], True

    # ---------- lookup ----------
    def company_by_identifier(self, scheme, value):
        row = self._q("SELECT company_id FROM company_identifier WHERE scheme=? AND value=?",
                              (scheme, str(value))).fetchone()
        return row["company_id"] if row else None

    def companies_by_name(self, norm, country):
        """Exact normalized name (canonical or alias) within the same country (NULL matches NULL only)."""
        q = """SELECT DISTINCT c.id FROM company c LEFT JOIN company_alias a ON a.company_id = c.id
               WHERE (c.name_norm = ? OR a.alias_norm = ?) AND c.country IS ?"""
        return [r["id"] for r in self._q(q, (norm, norm, country))]

    def seen_discovery_source_key(self, source, source_key):
        """A source/event pair is observed once; HTML decoration cannot duplicate it."""
        return bool(self._q(
            "SELECT 1 FROM observation WHERE source=? AND source_key=? LIMIT 1",
            (source, source_key)).fetchone())

    def companies_by_discovery_name(self, norm):
        """Exact normalized company/alias match, never filtered by event country."""
        return [r["id"] for r in self._q(
            """SELECT DISTINCT c.id FROM company c LEFT JOIN company_alias a ON a.company_id=c.id
               WHERE c.name_norm=? OR a.alias_norm=? ORDER BY c.id""", (norm, norm))]

    def add_discovery_candidate(self, observation_id, discovery, status, company_id=None, reason=None):
        self._q("""INSERT OR IGNORE INTO discovery_candidate
            (observation_id,name,name_norm,reported_country,website,role,status,
             matched_company_id,match_reason,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (observation_id, discovery.name, name_norm(discovery.name),
             discovery.reported_country, discovery.website, discovery.role,
             status, company_id, reason, now(), now()))

    def identifiers_of(self, company_id):
        return [(r["scheme"], r["value"]) for r in self._q(
            "SELECT scheme, value FROM company_identifier WHERE company_id=?", (company_id,))]

    def get(self, company_id):
        return self._q("SELECT * FROM company WHERE id=?", (company_id,)).fetchone()

    # ---------- writes ----------
    def create_company(self, name, country=None, city=None, website=None, notion_page_id=None,
                       flags=None, supply_role=None):
        cid = new_company_id()
        ts = now()
        if country == "UA":
            city = None  # no addresses for Ukrainian enterprises
        self._q(
            """INSERT INTO company(id, name, name_norm, country, city, website, notion_page_id, flags,
               supply_role, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (cid, name, name_norm(name), country, city, website, notion_page_id,
             json.dumps(sorted(set(flags or []))), supply_role, ts, ts))
        return cid

    def add_identifier(self, company_id, scheme, value, source):
        """Attach identifier; returns False if it already belongs to another company (no reassignment)."""
        owner = self.company_by_identifier(scheme, value)
        if owner and owner != company_id:
            return False
        if not owner:
            self._q("INSERT INTO company_identifier VALUES (?,?,?,?,?)",
                            (scheme, str(value), company_id, source, now()))
        return True

    def add_alias(self, company_id, alias, source):
        norm = name_norm(alias)
        if norm:
            self._q("INSERT OR IGNORE INTO company_alias VALUES (?,?,?,?)", (company_id, alias, norm, source))

    def add_signal(self, company_id, type_, layer, observation_id, counterpart=None, date=None, url=None):
        self._q(
            """INSERT OR IGNORE INTO signal(company_id, type, layer, counterpart, signal_date, url, observation_id)
               VALUES (?,?,?,?,?,?,?)""", (company_id, type_, layer, counterpart, _iso_date(date), url, observation_id))

    def add_category(self, company_id, axis, value, basis):
        self._q("INSERT OR IGNORE INTO company_category VALUES (?,?,?,?)", (company_id, axis, value, basis))

    def add_relation(self, from_id, to_id, relation, source):
        if from_id != to_id:
            self._q("INSERT OR IGNORE INTO company_relation VALUES (?,?,?,?)", (from_id, to_id, relation, source))

    def add_ownership_link(self, company_id, owner_country, link_type, source, owner_name=None, share_text=None):
        self._q("INSERT OR IGNORE INTO company_ownership_link VALUES (?,?,?,?,?,?,?)",
                        (company_id, owner_country, owner_name, share_text, link_type, source, now()))

    def add_match_candidate(self, observation_id, company_id, reason, mention_name):
        self._q("INSERT OR IGNORE INTO match_candidate VALUES (?,?,?,?)",
                        (observation_id, company_id, reason, mention_name))

    def update(self, company_id, **fields):
        if not fields:
            return
        fields["updated_at"] = now()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._q(f"UPDATE company SET {cols} WHERE id=?", (*fields.values(), company_id))

    def fill_missing(self, company_id, **fields):
        """Set fields only where the company has none yet (never overwrite)."""
        row = self.get(company_id)
        todo = {k: v for k, v in fields.items() if v and not row[k]}
        if todo.get("city") and (todo.get("country") or row["country"]) == "UA":
            todo.pop("city")
        self.update(company_id, **todo)

    def add_flags(self, company_id, flags):
        row = self.get(company_id)
        current = row["flags"]
        current = json.loads(current) if isinstance(current, str) else (current or [])
        merged = sorted(set(current) | set(flags))
        self.update(company_id, flags=json.dumps(merged))

    # ---------- source state / runs ----------
    def source_state(self, source):
        row = self._q("SELECT * FROM source_state WHERE source=?", (source,)).fetchone()
        return dict(row) if row else {"source": source, "cursor": None, "consecutive_failures": 0, "disabled": 0}

    def save_source_state(self, source, cursor, failures, disabled):
        self._q("""INSERT INTO source_state VALUES (?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET
                           cursor=excluded.cursor, consecutive_failures=excluded.consecutive_failures,
                           disabled=excluded.disabled, updated_at=excluded.updated_at""",
                        (source, cursor, failures, bool(disabled), now()))

    def save_run(self, **row):
        cols = ", ".join(row)
        self._q(f"INSERT INTO run({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def query(self, sql, params=()):
        return [dict(r) for r in self._q(sql, params)]
