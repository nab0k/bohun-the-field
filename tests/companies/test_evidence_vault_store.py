"""Offline tests for ciphertext-only vault migration and guarded upload."""
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from collectors.companies import evidence_archive as archive
from collectors.companies import evidence_vault_store as vault
from tests.companies.test_evidence_archive import fake_fetch


class FakeDb:
    def __init__(self, schema=True, ledger=True):
        self.schema, self.ledger = schema, ledger
        self.items = {}
        self.calls = []
        self.commits = self.rollbacks = 0

    def execute(self, sql, params=()):
        self.calls.append(sql)
        if "to_regclass" in sql:
            return FakeCursor([{"t": "companies.encrypted_evidence_archive" if self.schema else None}])
        if "SELECT version FROM companies.schema_migration" in sql:
            return FakeCursor([{"version": 3}] if self.ledger else [])
        if sql.startswith("SELECT cipher_sha256"):
            digest = params[0]
            return FakeCursor([self.items[digest]] if digest in self.items else [])
        if sql.startswith("INSERT INTO companies.encrypted_evidence_archive"):
            digest, cipher, _, cert_hash, batch, file_count, n = params
            self.items[digest] = {"cipher_sha256": digest, "cipher_bytes": n,
                                  "recipient_cert_sha256": cert_hash,
                                  "file_count": file_count}
            assert batch == "DISCOVERY-ENRICH-03"
            assert isinstance(cipher, bytes)
            assert not cipher.startswith(b"<html>")
        return FakeCursor()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeCursor:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL required")
class TestEncryptedVaultOnly(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.folder.name)
        self.root = root
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048",
                        "-nodes", "-keyout", str(root/"key.pem"), "-out", str(root/"cert.pem"),
                        "-days", "1", "-subj", "/CN=private-vault-test"],
                       check=True, capture_output=True)
        archive.collect_twenty(root/"private", fetcher=fake_fetch)
        self.manifest = archive.encrypted_export(root/"private", root/"cipher",
                                                 cert_path=root/"cert.pem")
        self.cipher_path = next((root/"cipher").glob("*.cms.der"))
        self.manifest_path = next((root/"cipher").glob("manifest-*.json"))

    def tearDown(self):
        self.folder.cleanup()

    def test_approval_is_required_before_any_db_touch(self):
        conn = FakeDb()
        with self.assertRaises(PermissionError):
            vault.upload_encrypted(self.cipher_path, self.manifest_path,
                                   conn=conn, approved=False)
        self.assertEqual(conn.calls, [])

    def test_upload_only_ciphertext_and_idempotent(self):
        conn = FakeDb()
        result = vault.upload_encrypted(self.cipher_path, self.manifest_path,
                                        conn=conn, approved=True)
        self.assertEqual(result["status"], "ciphertext_inserted")
        self.assertEqual(conn.commits, 1)
        self.assertEqual(len(conn.items), 1)
        replay = vault.upload_encrypted(self.cipher_path, self.manifest_path,
                                        conn=conn, approved=True)
        self.assertEqual(replay["status"], "already_present")
        self.assertEqual(len(conn.items), 1)
        self.assertFalse(any("company SET" in sql or "UPDATE company" in sql for sql in conn.calls))

    def test_missing_003_fails_closed(self):
        for schema, ledger in ((False, True), (True, False)):
            conn = FakeDb(schema=schema, ledger=ledger)
            with self.assertRaises(RuntimeError):
                vault.upload_encrypted(self.cipher_path, self.manifest_path,
                                       conn=conn, approved=True)
            self.assertEqual(conn.commits, 0)
            self.assertGreaterEqual(conn.rollbacks, 1)

    def test_corrupted_cipher_not_inserted(self):
        modified = bytearray(self.cipher_path.read_bytes())
        modified[-3] ^= 10
        self.cipher_path.write_bytes(modified)
        conn = FakeDb()
        with self.assertRaises(ValueError):
            vault.upload_encrypted(self.cipher_path, self.manifest_path,
                                   conn=conn, approved=True)
        self.assertEqual(conn.calls, [])

    def test_ciphertext_only_no_legal_facts_in_metadata(self):
        self.assertEqual(self.manifest["company_writes"], 0)
        self.assertFalse(self.manifest["durable_remote_copy_verified"])
        self.assertNotIn("source_url", self.manifest)
        self.assertNotIn("candidate_id", self.manifest)


if __name__ == "__main__":
    unittest.main()
