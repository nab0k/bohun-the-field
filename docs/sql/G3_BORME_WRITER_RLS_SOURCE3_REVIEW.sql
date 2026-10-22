-- BOHUN G3: REVIEW ONLY. Apply AFTER corporate_actions migration, NOT in production without approval.
-- Prerequisite: intelligence.sources(id=3,name='BORME'); direct PostgreSQL login provisioned separately.
BEGIN;
DO $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM intelligence.sources WHERE id=3 AND name='BORME' AND active) THEN
  RAISE EXCEPTION 'Expected active BORME source_id=3; abort';
 END IF;
 IF to_regclass('intelligence.corporate_actions') IS NULL THEN
  RAISE EXCEPTION 'G3 corporate_actions missing; abort';
 END IF;
 IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='bohun_borme_ingest_login') THEN
  RAISE EXCEPTION 'BORME login already exists; review credentials and grants manually';
 END IF;
END $$;
-- Provision login credentials through a controlled secret-management step; do not commit passwords.
CREATE ROLE bohun_borme_ingest_login NOLOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT USAGE ON SCHEMA intelligence TO bohun_borme_ingest_login;
GRANT SELECT (id, source_id) ON intelligence.items TO bohun_borme_ingest_login;
GRANT SELECT, INSERT, UPDATE ON intelligence.corporate_actions TO bohun_borme_ingest_login;
GRANT USAGE ON SEQUENCE intelligence.corporate_actions_id_seq TO bohun_borme_ingest_login;
CREATE POLICY borme_ingest_items_select ON intelligence.items FOR SELECT TO bohun_borme_ingest_login
 USING (source_id = 3);
CREATE POLICY borme_ingest_actions_select ON intelligence.corporate_actions FOR SELECT TO bohun_borme_ingest_login
 USING (item_id IN (SELECT id FROM intelligence.items) AND extraction_status <> 'rejected');
CREATE POLICY borme_ingest_actions_insert ON intelligence.corporate_actions FOR INSERT TO bohun_borme_ingest_login
 WITH CHECK (item_id IN (SELECT id FROM intelligence.items) AND extraction_status = 'unreviewed');
CREATE POLICY borme_ingest_actions_update ON intelligence.corporate_actions FOR UPDATE TO bohun_borme_ingest_login
 USING (item_id IN (SELECT id FROM intelligence.items) AND extraction_status = 'unreviewed')
 WITH CHECK (item_id IN (SELECT id FROM intelligence.items) AND extraction_status IN ('unreviewed','reviewed'));
COMMIT;
-- After approval, provision LOGIN and password using a secret manager, or prefer dedicated credential workflow.
-- No GRANT to service_role, anon or authenticated. No DELETE.
