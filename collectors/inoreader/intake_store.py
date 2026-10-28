"""Narrow DB adapter; no driver import, connection or retry at import time."""
from .intake_contract import (IntakeError, PreparedBatch, require, sha256, uuid_string,
                              validate_batch, validate_manifest)


class CommitUnknown(IntakeError):
    def __init__(self):
        super().__init__("INTAKE_COMMIT_UNKNOWN_READ_ID_NO_RETRY")


class PostCommitUnconfirmed(IntakeError):
    def __init__(self):
        super().__init__("INTAKE_POSTCOMMIT_UNCONFIRMED_READ_ID_NO_RETRY")


def validate_receipt(receipt, expected_id):
    require(isinstance(receipt, dict) and set(receipt) ==
            {"batch_id", "manifest_json", "manifest_sha256", "items"}, "INTAKE_RECEIPT_INVALID")
    require(receipt["batch_id"] == expected_id, "INTAKE_RECEIPT_ID_MISMATCH")
    manifest = validate_manifest(receipt["manifest_json"], receipt["manifest_sha256"])
    require(manifest["batch_id"] == expected_id and isinstance(receipt["items"], list)
            and len(receipt["items"]) == manifest["item_count"], "INTAKE_RECEIPT_INVALID")
    for member, actual in zip(manifest["items"], receipt["items"]):
        require(isinstance(actual, dict) and set(actual) ==
                {"ordinal", "artifact_id", "job_id", "payload_sha256"}, "INTAKE_RECEIPT_INVALID")
        require(type(actual["ordinal"]) is int and actual["ordinal"] == member["ordinal"]
                and actual["payload_sha256"] == member["payload_sha256"], "INTAKE_RECEIPT_INVALID")
        uuid_string(actual["artifact_id"])
        uuid_string(actual["job_id"])
    return manifest


class PostgresIntakeStore:
    """Factory must return a fresh psycopg-like connection with autocommit=False.

    Only exact private function calls are issued. Real SQL/ACL acceptance remains
    separate from fault-injection tests; this is not the A18 worker Store.
    """
    def __init__(self, factory):
        self.factory = factory

    @staticmethod
    def _close(conn):
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass  # Never replace COMMIT uncertainty with a close error.

    def _open(self, readonly):
        conn = self.factory()
        try:
            require(conn.autocommit is False, "INTAKE_AUTOCOMMIT_FORBIDDEN")
            if readonly:
                conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL statement_timeout = '15s'")
            conn.execute("SET LOCAL lock_timeout = '5s'")
            row = conn.execute("SELECT current_user, session_user").fetchone()
            require(row == ("bohun_intake", "bohun_intake"), "INTAKE_LIMITED_ROLE_REQUIRED")
            return conn
        except Exception:
            self._close(conn)
            raise

    @staticmethod
    def _one(conn, query, params):
        row = conn.execute(query, params).fetchone()
        require(isinstance(row, (tuple, list)) and len(row) == 1, "INTAKE_DB_REPLY_INVALID")
        return row[0]

    def _load(self, conn, batch_id, receipt=None):
        if receipt is None:
            receipt = self._one(conn, "SELECT exp009.read_intake(%s::uuid)", (batch_id,))
        if receipt is None:
            return None
        validate_receipt(receipt, batch_id)
        payloads = []
        for member in receipt["items"]:
            payload = self._one(conn, "SELECT exp009.read_artifact(%s::uuid, %s::text)",
                                (member["artifact_id"], member["payload_sha256"]))
            require(isinstance(payload, str) and sha256(payload) == member["payload_sha256"],
                    "INTAKE_STORED_PAYLOAD_MISMATCH")
            payloads.append(payload)
        batch = PreparedBatch(receipt["manifest_json"], receipt["manifest_sha256"], tuple(payloads))
        validate_batch(batch)
        return batch, receipt

    def read(self, batch_id):
        uuid_string(batch_id)
        conn = None
        try:
            conn = self._open(readonly=True)
            result = self._load(conn, batch_id)
            conn.rollback()  # Ends a read-only transaction, never commits a write.
            return result
        except IntakeError:
            raise
        except Exception:
            raise IntakeError("INTAKE_READ_FAILED_NO_RETRY") from None
        finally:
            self._close(conn)

    def persist(self, batch):
        validate_batch(batch)  # No connection for invalid local input.
        conn = None
        try:
            conn = self._open(readonly=False)
            receipt = self._one(conn,
                                "SELECT exp009.persist_intake(%s::text, %s::text, %s::text[])",
                                (batch.manifest_json, batch.manifest_sha256, list(batch.payloads)))
            result = self._load(conn, batch.batch_id, receipt)
            require(result is not None and result[0] == batch, "INTAKE_STORED_BATCH_MISMATCH")
            try:
                conn.commit()
            except Exception:
                raise CommitUnknown() from None
        except CommitUnknown:
            raise  # No reconnect/retry/provider fetch after an ambiguous COMMIT.
        except IntakeError:
            raise
        except Exception:
            raise IntakeError("INTAKE_WRITE_FAILED_NO_RETRY") from None
        finally:
            # Closing an uncommitted connection rolls its transaction back.
            self._close(conn)
        try:
            confirmed = self.read(batch.batch_id)  # Fresh, read-only connection.
            require(confirmed is not None and confirmed[0] == batch
                    and confirmed[1] == receipt, "INTAKE_POSTCOMMIT_MISMATCH")
        except Exception:
            raise PostCommitUnconfirmed() from None
        return receipt
