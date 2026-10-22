"""Offline migration-runner checks; no real database, network or secrets."""
import hashlib
import pathlib
import unittest

from collectors.companies.pg_store import PgStore


class FakeResult:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, has_schema=True, versions=(), fail_002=False, missing_tables=()):
        self.has_schema = has_schema
        self.missing_tables = set(missing_tables)
        self.versions = set(versions)
        self.fail_002 = fail_002
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        if "SELECT to_regnamespace('companies')" in sql:
            return FakeResult([{"ok": self.has_schema}])
        if "SELECT to_regclass(%s)" in sql:
            return FakeResult([{"ok": params[0] not in self.missing_tables}])
        if sql.startswith("SELECT version FROM companies.schema_migration"):
            return FakeResult([{"version": v} for v in sorted(self.versions)])
        if sql.startswith("INSERT INTO companies.schema_migration"):
            self.versions.add(int(params[0]) if params else 1)
        if "CREATE SCHEMA IF NOT EXISTS companies" in sql:
            self.has_schema = True
        if self.fail_002 and "ALTER TABLE companies.run ADD COLUMN IF NOT EXISTS discoveries" in sql:
            raise RuntimeError("simulated failed additive migration")
        return FakeResult()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def new_store(conn):
    store = PgStore.__new__(PgStore)
    store.db = conn
    return store


class TestNumberedMigrations(unittest.TestCase):
    def test_legacy_schema_default_applies_only_002_then_noop(self):
        conn = FakeConnection(has_schema=True)
        store = new_store(conn)
        self.assertEqual(store.migrate(), "applied:2")
        sqls = [sql for sql, _ in conn.statements]
        self.assertTrue(any("ADD COLUMN IF NOT EXISTS discoveries" in sql for sql in sqls))
        self.assertFalse(any("cms_ciphertext BYTEA" in sql for sql in sqls))
        self.assertFalse(any("CREATE SCHEMA IF NOT EXISTS companies" in sql for sql in sqls))
        self.assertEqual(conn.versions, {1, 2})
        self.assertEqual(store.migrate(), "exists")
        sqls = [sql for sql, _ in conn.statements]
        self.assertEqual(sum("ADD COLUMN IF NOT EXISTS discoveries" in sql for sql in sqls), 1)
        self.assertEqual(conn.rollbacks, 0)

    def test_incomplete_legacy_schema_fails_closed(self):
        conn = FakeConnection(has_schema=True, missing_tables=("companies.observation",))
        with self.assertRaisesRegex(RuntimeError, "missing observation"):
            new_store(conn).migrate()
        self.assertEqual(conn.rollbacks, 1)
        self.assertEqual(conn.commits, 0)
        self.assertFalse(any("ADD COLUMN IF NOT EXISTS" in sql for sql, _ in conn.statements))

    def test_new_install_applies_001_and_002_only(self):
        conn = FakeConnection(has_schema=False)
        self.assertEqual(new_store(conn).migrate(), "applied:1,2")
        self.assertEqual(conn.versions, {1, 2})
        self.assertTrue(conn.has_schema)
        self.assertEqual(conn.commits, 1)

    def test_003_requires_exact_reviewed_sha_and_prior_schema(self):
        sql = pathlib.Path(__file__).resolve().parents[2] / "db/companies/003_encrypted_evidence_archive.sql"
        approved = hashlib.sha256(sql.read_bytes()).hexdigest()
        conn = FakeConnection(has_schema=True, versions=(1, 2))
        store = new_store(conn)
        for bad in ("invalid", "0" * 64, "A" * 64):
            with self.assertRaises(PermissionError):
                store.migrate(approved_evidence_003_sha256=bad)
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.statements, [])
        self.assertEqual(store.migrate(approved_evidence_003_sha256=approved), "applied:3")
        self.assertEqual(conn.versions, {1, 2, 3})
        self.assertEqual(store.migrate(approved_evidence_003_sha256=approved), "exists")
        self.assertEqual(sum("cms_ciphertext BYTEA" in s for s, _ in conn.statements), 1)

        for fresh, versions in ((False, ()), (True, (1,))):
            unsafe = FakeConnection(has_schema=fresh, versions=versions)
            with self.assertRaises(RuntimeError):
                new_store(unsafe).migrate(approved_evidence_003_sha256=approved)
            self.assertEqual(unsafe.commits, 0)
            self.assertEqual(unsafe.rollbacks, 1)
            self.assertFalse(any("cms_ciphertext BYTEA" in s for s, _ in unsafe.statements))

    def test_failure_rolls_back_instead_of_reset(self):
        conn = FakeConnection(has_schema=True, fail_002=True)
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            new_store(conn).migrate()
        self.assertEqual(conn.commits, 0)
        self.assertEqual(conn.rollbacks, 1)
        self.assertFalse(any("DROP SCHEMA" in sql for sql, _ in conn.statements))

    def test_002_is_additive_and_locked_down(self):
        path = pathlib.Path(__file__).resolve().parents[2] / "db/companies/002_discovery_intake.sql"
        sql = path.read_text(encoding="utf-8").upper()
        self.assertIn("CREATE TABLE IF NOT EXISTS", sql)
        self.assertIn("ADD COLUMN IF NOT EXISTS", sql)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        for word in ("DROP SCHEMA", "DROP TABLE", "TRUNCATE ", "DELETE FROM"):
            self.assertNotIn(word, sql)

    def test_003_is_private_additive_ciphertext_only(self):
        path = pathlib.Path(__file__).resolve().parents[2] / "db/companies/003_encrypted_evidence_archive.sql"
        sql = path.read_text(encoding="utf-8").upper()
        self.assertIn("CREATE TABLE IF NOT EXISTS", sql)
        self.assertIn("ENABLE ROW LEVEL SECURITY", sql)
        self.assertIn("CMS_CIPHERTEXT BYTEA", sql)
        self.assertIn("REVOKE ALL", sql)
        self.assertNotIn("GRANT SELECT", sql)
        for word in ("DROP TABLE", "DROP SCHEMA", "TRUNCATE ", "DELETE FROM"):
            self.assertNotIn(word, sql)

    def test_003_runbook_command_has_single_line_continuations(self):
        path = pathlib.Path(__file__).resolve().parents[2] / "docs/DISCOVERY-ENRICH-03-RUNBOOK.md"
        doc = path.read_text(encoding="utf-8")
        block = doc.split("python -m collectors.companies.evidence_vault_store ", 1)[1]
        lines = block.split("```", 1)[0].splitlines()
        # The command's first line and every continued argument line end in one
        # literal backslash. A double backslash ends the Bash command early.
        self.assertTrue(lines[0] == "\\")
        self.assertTrue(lines[1].endswith(" \\") and not lines[1].endswith(" \\\\"))
        self.assertTrue(lines[2].endswith(" \\") and not lines[2].endswith(" \\\\"))
        self.assertIn("--approve-upload-ciphertext", lines[3])

    def test_sqlite_mirror_preserves_discovery_separation(self):
        from collectors.companies.store import SqliteStore
        s = SqliteStore()
        names = {r["name"] for r in s.query("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn("discovery_candidate", names)
        self.assertIn("schema_migration", names)
        cols = {r["name"] for r in s.query("PRAGMA table_info(run)")}
        self.assertTrue({"discoveries", "candidate_existing", "holds"} <= cols)


if __name__ == "__main__":
    unittest.main()
