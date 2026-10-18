"""PostgreSQL (Supabase) store for the companies pipeline (EXP-012, TODO v1 T08).

Same methods as SqliteStore. Every SQL statement goes through `_q`, which translates the
SQLite dialect used there into PostgreSQL. Tables live in the isolated schema `companies`
(db/companies/001_companies_schema.sql); no other schema is read or written.

Connection follows the existing collectors (collectors/inoreader/sync.py): Supabase pooler,
password only from the SUPABASE_DB_PASSWORD environment variable (GitHub Actions secret).
"""
import os
import pathlib
import re

from .store import SqliteStore

MIGRATION = pathlib.Path(__file__).resolve().parents[2] / "db" / "companies" / "001_companies_schema.sql"

_OR_IGNORE = re.compile(r"^\s*INSERT OR IGNORE INTO", re.I)


def translate(sql):
    """SQLite dialect of store.py -> PostgreSQL. Covers only the constructs store.py uses."""
    out = sql
    if _OR_IGNORE.match(out):
        out = _OR_IGNORE.sub("INSERT INTO", out, count=1).rstrip() + " ON CONFLICT DO NOTHING"
    out = re.sub(r"\bIS \?", "IS NOT DISTINCT FROM ?", out)
    if "%" in out:
        raise ValueError("literal % is not supported in translated SQL")
    return out.replace("?", "%s")


def connect_params():
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    if not password:
        raise RuntimeError("Missing SUPABASE_DB_PASSWORD")
    return dict(
        host=os.environ.get("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
        port=int(os.environ.get("SUPABASE_DB_PORT", "5432")),
        dbname="postgres",
        user=os.environ.get("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
        password=password, sslmode="require", connect_timeout=20,
    )


class PgStore(SqliteStore):
    def __init__(self, conn=None):  # noqa: D401 — no SQLite schema here; the migration owns the schema
        if conn is None:
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(**connect_params(), row_factory=dict_row)
        self.db = conn
        self.db.execute("SET search_path TO companies")
        self.db.commit()  # keep the setting outside the first work transaction

    def _q(self, sql, params=()):
        return self.db.execute(translate(sql), params)

    def schema_exists(self):
        row = self.db.execute("SELECT to_regnamespace('companies') IS NOT NULL AS ok").fetchone()
        return bool(row["ok"])

    def migrate(self, path=MIGRATION):
        """Apply the schema once, in one transaction. Returns 'applied' or 'exists'."""
        if self.schema_exists():
            return "exists"
        self.db.execute(pathlib.Path(path).read_text(encoding="utf-8"))
        self.db.commit()
        self.db.execute("SET search_path TO companies")
        self.db.commit()
        return "applied"
