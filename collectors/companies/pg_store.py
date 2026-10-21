"""Supabase PostgreSQL companies store with additive, versioned migrations."""
import os
import pathlib
import re

from .store import SqliteStore

MIGRATIONS = pathlib.Path(__file__).resolve().parents[2] / "db" / "companies"
_OR_IGNORE = re.compile(r"^\s*INSERT OR IGNORE INTO", re.I)


def translate(sql):
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
    def __init__(self, conn=None):
        if conn is None:
            import psycopg
            from psycopg.rows import dict_row
            conn = psycopg.connect(**connect_params(), row_factory=dict_row)
        self.db = conn
        self.db.execute("SET search_path TO companies")
        self.db.commit()

    def _q(self, sql, params=()):
        return self.db.execute(translate(sql), params)

    def schema_exists(self):
        row = self.db.execute("SELECT to_regnamespace('companies') IS NOT NULL AS ok").fetchone()
        return bool(row["ok"])

    def reset_staging(self):
        """Legacy EXP-012 method; never used by discovery."""
        self.db.execute("DROP SCHEMA IF EXISTS companies CASCADE")
        self.db.commit()
        return "dropped"

    def migrate(self, path=None):
        """001 on fresh schema; 002 on existing legacy schema; transactional and no reset."""
        folder = pathlib.Path(path).parent if path else MIGRATIONS
        scripts = sorted(folder.glob("[0-9][0-9][0-9]_*.sql"))
        nums = [int(f.name[:3]) for f in scripts]
        if not scripts or nums != list(range(1, len(scripts) + 1)):
            raise ValueError("numbered migrations must be contiguous beginning at 001")
        try:
            was_present = self.schema_exists()
            completed = []
            if was_present:
                # A namespace is not sufficient evidence that migration 001 completed.
                for table in ("company", "observation", "run", "source_state", "company_alias"):
                    r = self.db.execute("SELECT to_regclass(%s) IS NOT NULL AS ok",
                                        (f"companies.{table}",)).fetchone()
                    if not r["ok"]:
                        raise RuntimeError(f"incomplete existing companies schema: missing {table}")
            if not was_present:
                self.db.execute(scripts[0].read_text(encoding="utf-8"))
                completed.append(1)
            self.db.execute("""CREATE TABLE IF NOT EXISTS companies.schema_migration(
                version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
            seen = {int(r["version"]) for r in
                    self.db.execute("SELECT version FROM companies.schema_migration").fetchall()}
            if not seen:
                self.db.execute("INSERT INTO companies.schema_migration(version) VALUES (1)")
                seen.add(1)
            for num, script in zip(nums[1:], scripts[1:]):
                if num not in seen:
                    self.db.execute(script.read_text(encoding="utf-8"))
                    self.db.execute("INSERT INTO companies.schema_migration(version) VALUES (%s)", (num,))
                    seen.add(num)
                    completed.append(num)
            self.db.commit()
            return "applied:" + ",".join(str(x) for x in completed) if completed else "exists"
        except Exception:
            self.db.rollback()
            raise
