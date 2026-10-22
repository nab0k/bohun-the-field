\set ON_ERROR_STOP on
-- Disposable PostgreSQL CI fixture. This is NOT a production credential or role.
ALTER TABLE intelligence.items ADD COLUMN source_id bigint;
UPDATE intelligence.items SET source_id=77 WHERE id=1001;
UPDATE intelligence.items SET source_id=88 WHERE id=1002;
ALTER TABLE intelligence.items ALTER COLUMN source_id SET NOT NULL;
CREATE ROLE bohun_borme_ingest_login LOGIN NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT USAGE ON SCHEMA intelligence TO bohun_borme_ingest_login;
GRANT SELECT (id,source_id) ON intelligence.items TO bohun_borme_ingest_login;
ALTER TABLE intelligence.items ENABLE ROW LEVEL SECURITY;
ALTER TABLE intelligence.items FORCE ROW LEVEL SECURITY;
CREATE POLICY g3_borme_item_read ON intelligence.items FOR SELECT TO bohun_borme_ingest_login
 USING (source_id=77);
GRANT SELECT,INSERT,UPDATE ON intelligence.corporate_actions TO bohun_borme_ingest_login;
GRANT USAGE ON SEQUENCE intelligence.corporate_actions_id_seq TO bohun_borme_ingest_login;
CREATE POLICY g3_borme_action_select ON intelligence.corporate_actions FOR SELECT TO bohun_borme_ingest_login
 USING (item_id IN (SELECT id FROM intelligence.items) AND extraction_status <> 'rejected');
CREATE POLICY g3_borme_action_insert ON intelligence.corporate_actions FOR INSERT TO bohun_borme_ingest_login
 WITH CHECK (item_id IN (SELECT id FROM intelligence.items) AND extraction_status='unreviewed');
CREATE POLICY g3_borme_action_update ON intelligence.corporate_actions FOR UPDATE TO bohun_borme_ingest_login
 USING (item_id IN (SELECT id FROM intelligence.items) AND extraction_status='unreviewed')
 WITH CHECK (item_id IN (SELECT id FROM intelligence.items) AND extraction_status IN ('unreviewed','reviewed'));
-- Top-level SET ROLE ensures the query planner and RLS use the tested role.
DO $$
BEGIN
 IF (SELECT rolbypassrls FROM pg_roles WHERE rolname='bohun_borme_ingest_login') THEN RAISE EXCEPTION 'BYPASSRLS enabled'; END IF;
 IF has_table_privilege('bohun_borme_ingest_login','intelligence.items','INSERT') THEN RAISE EXCEPTION 'items write privilege leaked'; END IF;
 IF has_table_privilege('bohun_borme_ingest_login','intelligence.corporate_actions','DELETE') THEN RAISE EXCEPTION 'actions DELETE leaked'; END IF;
END $$;
SET ROLE bohun_borme_ingest_login;
DO $$
DECLARE n bigint;
BEGIN
 SELECT count(*) INTO n FROM intelligence.items;
 IF n <> 1 THEN RAISE EXCEPTION 'expected one BORME item, got %',n; END IF;
END $$;
INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method)
 VALUES (1001,71,'test','ci');
DO $$
DECLARE n bigint;
BEGIN
 SELECT count(*) INTO n FROM intelligence.corporate_actions WHERE item_id=1001 AND action_ordinal=71;
 IF n <> 1 THEN RAISE EXCEPTION 'cannot see own BORME action'; END IF;
END $$;
RESET ROLE;
-- Negative cross-source INSERT is checked in the CI shell with SET ROLE and an expected error.
SELECT 'PASS: source-scoped RLS login positive path' AS result;
