\set ON_ERROR_STOP on
-- Disposable CI only: dedicated NOLOGIN role without BYPASSRLS.
CREATE ROLE bohun_borme_writer NOLOGIN NOBYPASSRLS;
GRANT USAGE ON SCHEMA intelligence TO bohun_borme_writer;
GRANT SELECT ON intelligence.items TO bohun_borme_writer;
GRANT SELECT, INSERT, UPDATE ON intelligence.corporate_actions TO bohun_borme_writer;
GRANT USAGE ON SEQUENCE intelligence.corporate_actions_id_seq TO bohun_borme_writer;
-- Intentionally no DELETE, no DDL, no access to unrelated tables.
CREATE POLICY borme_writer_read ON intelligence.corporate_actions
 FOR SELECT TO bohun_borme_writer USING (extraction_status <> 'rejected');
CREATE POLICY borme_writer_insert ON intelligence.corporate_actions
 FOR INSERT TO bohun_borme_writer WITH CHECK (extraction_status = 'unreviewed');
CREATE POLICY borme_writer_update ON intelligence.corporate_actions
 FOR UPDATE TO bohun_borme_writer
 USING (extraction_status = 'unreviewed')
 WITH CHECK (extraction_status IN ('unreviewed','reviewed'));
DO $$
DECLARE n bigint; b boolean;
BEGIN
 IF (SELECT rolbypassrls FROM pg_roles WHERE rolname='bohun_borme_writer') THEN RAISE EXCEPTION 'writer bypasses RLS'; END IF;
 IF has_table_privilege('bohun_borme_writer','intelligence.corporate_actions','DELETE') THEN RAISE EXCEPTION 'writer has DELETE'; END IF;
 EXECUTE 'SET LOCAL ROLE bohun_borme_writer';
 INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method)
 VALUES (1002,40,'test','borme-ci');
 SELECT count(*) INTO n FROM intelligence.corporate_actions WHERE item_id=1002 AND action_ordinal=40;
 IF n <> 1 THEN RAISE EXCEPTION 'writer cannot read own insert'; END IF;
 UPDATE intelligence.corporate_actions SET extraction_status='reviewed'
 WHERE item_id=1002 AND action_ordinal=40;
 SELECT count(*) INTO n FROM intelligence.corporate_actions WHERE item_id=1002 AND action_ordinal=40 AND extraction_status='reviewed';
 IF n <> 1 THEN RAISE EXCEPTION 'review transition failed'; END IF;
 EXECUTE 'RESET ROLE';
END $$;
DO $$
DECLARE denied boolean;
BEGIN
 denied := false;
 BEGIN
  EXECUTE 'SET LOCAL ROLE bohun_borme_writer';
  INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method,extraction_status)
   VALUES (1002,41,'test','borme-ci','reviewed');
 EXCEPTION WHEN insufficient_privilege THEN denied := true;
 END;
 EXECUTE 'RESET ROLE';
 IF NOT denied THEN RAISE EXCEPTION 'writer could insert reviewed row'; END IF;
 denied := false;
 BEGIN
  EXECUTE 'SET LOCAL ROLE bohun_borme_writer';
  DELETE FROM intelligence.corporate_actions WHERE item_id=1002 AND action_ordinal=40;
 EXCEPTION WHEN insufficient_privilege THEN denied := true;
 END;
 EXECUTE 'RESET ROLE';
 IF NOT denied THEN RAISE EXCEPTION 'writer could DELETE'; END IF;
END $$;
-- Ensure writer cannot read rejected records, even with table SELECT grant.
INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method,extraction_status)
 VALUES (1002,42,'test','borme-ci','rejected');
DO $$
DECLARE n bigint;
BEGIN
 EXECUTE 'SET LOCAL ROLE bohun_borme_writer';
 SELECT count(*) INTO n FROM intelligence.corporate_actions WHERE item_id=1002 AND action_ordinal=42;
 EXECUTE 'RESET ROLE';
 IF n <> 0 THEN RAISE EXCEPTION 'writer can read rejected row'; END IF;
END $$;
SELECT 'PASS: dedicated NOLOGIN NOBYPASSRLS writer, INSERT/UPDATE allowed, DELETE/invalid INSERT/rejected SELECT denied' AS result;
