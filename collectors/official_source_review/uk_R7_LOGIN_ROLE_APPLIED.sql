-- EXP-009 R7: login for the UK staging writer. EXECUTED by the owner in the
-- Supabase SQL Editor on 2026-10-22 (reference copy; do not reapply).
-- Contains NO password or other secret. The password was set separately as a
-- SCRAM verifier; the plain password exists only in the operator's Keychain.
BEGIN;

CREATE ROLE bohun_uk_ingest_login
    LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
    CONNECTION LIMIT 2;

-- All table rights come only from the existing NOLOGIN role (02_writer_permissions_APPLIED.sql).
GRANT bohun_uk_ingest_writer TO bohun_uk_ingest_login;

ALTER ROLE bohun_uk_ingest_login SET statement_timeout = '60s';
ALTER ROLE bohun_uk_ingest_login SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE bohun_uk_ingest_login SET lock_timeout = '10s';

COMMENT ON ROLE bohun_uk_ingest_login IS
    'EXP-009 UK FTS staging writer login; member of bohun_uk_ingest_writer only; manual pilot.';

COMMIT;

-- Password (owner only, never in a file, repo, log or chat): set as a SCRAM-SHA-256
-- verifier (ALTER ROLE ... PASSWORD 'SCRAM-SHA-256$...'), plain value kept in Keychain.
-- Pooler user name for Supavisor: bohun_uk_ingest_login.tecrucawzukxqtchpxrh
-- Env for the writer: BOHUN_UK_DB_WRITER_LOGIN=bohun_uk_ingest_login (bare name = current_user).
--
-- Rollback of this step (if needed, separately approved):
--   DROP ROLE bohun_uk_ingest_login;   -- owns no objects, only a membership
