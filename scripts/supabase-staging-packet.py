#!/usr/bin/env python3
"""Persist and independently verify one fail-closed contract packet in Supabase.

This script is intentionally limited to the isolated ``bohun_staging`` schema.
It never reads from or writes to production ``intelligence`` tables.
"""
import datetime
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

STAGING_SCHEMA = "bohun_staging"
OUT = Path("output/supabase-staging")
PACKET_PATH = Path("output/live-pilot/contract-packet.json")
LOCAL_DB_PATH = Path("output/live-pilot/pilot.sqlite3")
KEY_PATH = OUT / "packet-key.json"


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def connect():
    return psycopg.connect(
        host=os.getenv("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
        port=int(os.getenv("SUPABASE_DB_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
        password=os.environ["SUPABASE_DB_PASSWORD"],
        sslmode="require",
        connect_timeout=20,
    )


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_local_packet():
    packet = json.loads(PACKET_PATH.read_text())
    assert packet["internal_only"] is True
    assert len(packet["documents"]) == 1
    assert all(p["qa_status"] == "HOLD" for p in packet["publications"])
    assert all(p["public_release"] is False for p in packet["publications"])
    document = packet["documents"][0]
    with sqlite3.connect(LOCAL_DB_PATH) as local:
        row = local.execute(
            "SELECT payload,content_hash FROM raw_item WHERE id=?", (document["raw_item_id"],)
        ).fetchone()
    assert row, "LOCAL_RAW_DOCUMENT_NOT_FOUND"
    raw_content, content_sha256 = row
    assert hashlib.sha256(raw_content.encode()).hexdigest() == content_sha256
    assert document["content_sha256"] == content_sha256
    for evidence in packet["evidence"]:
        start = evidence["locator"]["char_start"]
        end = evidence["locator"]["char_end"]
        assert raw_content[start:end] == evidence["exact_excerpt"]
    packet_sha256 = hashlib.sha256(canonical_json(packet).encode()).hexdigest()
    return packet, document, raw_content, packet_sha256


def ensure_schema(cur):
    cur.execute("CREATE SCHEMA IF NOT EXISTS bohun_staging")
    cur.execute("REVOKE ALL ON SCHEMA bohun_staging FROM PUBLIC")
    cur.execute(
        """CREATE TABLE IF NOT EXISTS bohun_staging.raw_documents (
          document_id text PRIMARY KEY,
          source_id text NOT NULL,
          external_id text NOT NULL,
          content_sha256 char(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
          media_type text NOT NULL,
          original_url text NOT NULL,
          raw_content text NOT NULL,
          fetched_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          UNIQUE (source_id, external_id, content_sha256)
        )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS bohun_staging.contract_packets (
          packet_id char(64) PRIMARY KEY CHECK (packet_id ~ '^[0-9a-f]{64}$'),
          contract_version text NOT NULL,
          document_id text NOT NULL REFERENCES bohun_staging.raw_documents(document_id),
          payload jsonb NOT NULL,
          qa_status text NOT NULL CHECK (qa_status IN ('HOLD','APPROVE','REJECT')),
          public_release boolean NOT NULL DEFAULT false CHECK (public_release = false),
          evidence_count integer NOT NULL CHECK (evidence_count >= 0),
          claim_count integer NOT NULL CHECK (claim_count >= 0),
          last_run_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          last_seen_at timestamptz NOT NULL,
          UNIQUE (document_id, contract_version)
        )"""
    )
    cur.execute("REVOKE ALL ON ALL TABLES IN SCHEMA bohun_staging FROM PUBLIC")
    cur.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA bohun_staging REVOKE ALL ON TABLES FROM PUBLIC")


def write():
    OUT.mkdir(parents=True, exist_ok=True)
    packet, document, raw_content, packet_id = load_local_packet()
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    now = utc_now()
    with connect() as db:
        with db.cursor() as cur:
            ensure_schema(cur)
            cur.execute(
                """INSERT INTO bohun_staging.raw_documents
                  (document_id,source_id,external_id,content_sha256,media_type,original_url,raw_content,fetched_at)
                  VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                  ON CONFLICT (document_id) DO NOTHING RETURNING document_id""",
                (
                    document["document_id"], document["source_id"], document["external_id"],
                    document["content_sha256"], document["media_type"], document["original_url"],
                    raw_content, now,
                ),
            )
            raw_inserted = cur.fetchone() is not None
            cur.execute(
                """INSERT INTO bohun_staging.contract_packets
                  (packet_id,contract_version,document_id,payload,qa_status,public_release,
                   evidence_count,claim_count,last_run_id,last_seen_at)
                  VALUES (%s,%s,%s,%s,'HOLD',false,%s,%s,%s,%s)
                  ON CONFLICT (packet_id) DO UPDATE SET
                    last_run_id=EXCLUDED.last_run_id,last_seen_at=EXCLUDED.last_seen_at
                  RETURNING (xmax = 0) AS inserted""",
                (
                    packet_id, packet["contract_version"], document["document_id"], Jsonb(packet),
                    len(packet["evidence"]), len(packet["claims"]), run_id, now,
                ),
            )
            packet_inserted = cur.fetchone()[0]
            cur.execute(
                """SELECT COUNT(*) FROM bohun_staging.contract_packets
                   WHERE packet_id=%s AND qa_status='HOLD' AND public_release=false""",
                (packet_id,),
            )
            assert cur.fetchone()[0] == 1
    key = {
        "packet_id": packet_id,
        "document_id": document["document_id"],
        "content_sha256": document["content_sha256"],
        "run_id": run_id,
    }
    KEY_PATH.write_text(json.dumps(key, indent=2))
    print(json.dumps({
        "stage": "SUPABASE_STAGING_WRITE", "schema": STAGING_SCHEMA,
        "raw_inserted": raw_inserted, "packet_inserted": packet_inserted,
        "packet_id": packet_id, "qa_status": "HOLD", "public_release": False,
        "production_tables_touched": 0,
    }))


def readback():
    key = json.loads(KEY_PATH.read_text())
    with connect() as db:
        db.execute("SET default_transaction_read_only=on")
        with db.cursor() as cur:
            cur.execute(
                """SELECT p.payload,p.qa_status,p.public_release,p.evidence_count,p.claim_count,
                          d.raw_content,d.content_sha256,d.source_id,d.external_id
                   FROM bohun_staging.contract_packets p
                   JOIN bohun_staging.raw_documents d ON d.document_id=p.document_id
                   WHERE p.packet_id=%s AND p.document_id=%s""",
                (key["packet_id"], key["document_id"]),
            )
            row = cur.fetchone()
            assert row, "STAGING_PACKET_NOT_FOUND"
            payload, qa_status, public_release, evidence_count, claim_count, raw_content, content_sha256, source_id, external_id = row
            assert hashlib.sha256(raw_content.encode()).hexdigest() == content_sha256.strip()
            assert content_sha256.strip() == key["content_sha256"]
            assert hashlib.sha256(canonical_json(payload).encode()).hexdigest() == key["packet_id"].strip()
            assert qa_status == "HOLD" and public_release is False
            assert evidence_count == len(payload["evidence"])
            assert claim_count == len(payload["claims"])
            for evidence in payload["evidence"]:
                start = evidence["locator"]["char_start"]
                end = evidence["locator"]["char_end"]
                assert raw_content[start:end] == evidence["exact_excerpt"]
            cur.execute(
                """SELECT COUNT(*) FROM information_schema.role_table_grants
                   WHERE table_schema='bohun_staging' AND grantee IN ('PUBLIC','anon','authenticated')"""
            )
            public_grants = cur.fetchone()[0]
            assert public_grants == 0
    result = {
        "stage": "INDEPENDENT_SUPABASE_READBACK", "pass": True,
        "schema": STAGING_SCHEMA, "packet_id": key["packet_id"],
        "source_id": source_id, "external_id": external_id,
        "raw_hash_verified": True, "exact_evidence_verified": evidence_count,
        "claims_verified": claim_count, "qa_status": qa_status,
        "public_release": public_release, "public_role_grants": public_grants,
        "production_rows_read": 0, "production_tables_touched": 0,
    }
    (OUT / "readback.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"write", "readback"}:
        raise SystemExit("usage: supabase-staging-packet.py write|readback")
    {"write": write, "readback": readback}[sys.argv[1]]()
