\set ON_ERROR_STOP on
DO $$
DECLARE r text;
BEGIN
 IF NOT EXISTS (SELECT FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='intelligence' AND c.relname='corporate_actions' AND c.relrowsecurity AND c.relforcerowsecurity) THEN RAISE EXCEPTION 'RLS/FORCE RLS absent'; END IF;
 IF EXISTS (SELECT FROM pg_policies WHERE schemaname='intelligence' AND tablename='corporate_actions') THEN RAISE EXCEPTION 'unexpected RLS policy'; END IF;
 FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
  IF has_table_privilege(r,'intelligence.corporate_actions','SELECT') OR has_table_privilege(r,'intelligence.corporate_actions','INSERT') OR has_table_privilege(r,'intelligence.corporate_actions','UPDATE') OR has_table_privilege(r,'intelligence.corporate_actions','DELETE') THEN RAISE EXCEPTION 'table grant leaked to %',r; END IF;
  IF has_sequence_privilege(r,'intelligence.corporate_actions_id_seq','USAGE') OR has_sequence_privilege(r,'intelligence.corporate_actions_id_seq','SELECT') OR has_sequence_privilege(r,'intelligence.corporate_actions_id_seq','UPDATE') THEN RAISE EXCEPTION 'sequence grant leaked to %',r; END IF;
 END LOOP;
END $$;
INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method) VALUES (1001,1,'merger','manual');
DO $$
BEGIN
 IF (SELECT extraction_status FROM intelligence.corporate_actions WHERE item_id=1001 AND action_ordinal=1) <> 'unreviewed' THEN RAISE EXCEPTION 'default failed'; END IF;
 BEGIN
  INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method) VALUES (9999,1,'merger','manual');
  RAISE EXCEPTION 'missing FK accepted';
 EXCEPTION WHEN foreign_key_violation THEN NULL; END;
 BEGIN
  INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method) VALUES (1001,1,'merger','manual');
  RAISE EXCEPTION 'duplicate accepted';
 EXCEPTION WHEN unique_violation THEN NULL; END;
 BEGIN
  INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method) VALUES (1001,0,'merger','manual');
  RAISE EXCEPTION 'bad ordinal accepted';
 EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN
  INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method) VALUES (1001,2,'  ','manual');
  RAISE EXCEPTION 'blank type accepted';
 EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN
  INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method,extraction_status) VALUES (1001,2,'merger','manual','published');
  RAISE EXCEPTION 'bad status accepted';
 EXCEPTION WHEN check_violation THEN NULL; END;
 BEGIN
  DELETE FROM intelligence.items WHERE id=1001;
  RAISE EXCEPTION 'referenced item deleted';
 EXCEPTION WHEN foreign_key_violation THEN NULL; END;
END $$;
SELECT 'PASS: RLS metadata, grants, FK, UNIQUE, CHECK, defaults, RESTRICT' AS result;
