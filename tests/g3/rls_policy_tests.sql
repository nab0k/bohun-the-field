\set ON_ERROR_STOP on
-- Run only against disposable CI fixture. Temporary grants exist only in this database.
GRANT USAGE ON SCHEMA intelligence TO anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON intelligence.corporate_actions TO anon, authenticated, service_role;
GRANT USAGE, SELECT ON SEQUENCE intelligence.corporate_actions_id_seq TO anon, authenticated, service_role;
-- No policy: FORCE RLS denies access even with table/schema grants.
DO $$
DECLARE role_name text; n bigint;
BEGIN
 FOR role_name IN SELECT unnest(ARRAY['anon','authenticated','service_role']) LOOP
  EXECUTE format('SET LOCAL ROLE %I', role_name);
  EXECUTE 'SELECT count(*) FROM intelligence.corporate_actions' INTO n;
  IF n <> 0 THEN RAISE EXCEPTION 'FAIL: % can see % rows without policy',role_name,n; END IF;
  EXECUTE 'RESET ROLE';
 END LOOP;
END $$;
-- Explicit runtime test for denied INSERT as each role (check expected SQLSTATE).
DO $$
DECLARE role_name text; denied boolean;
BEGIN
 FOR role_name IN SELECT unnest(ARRAY['anon','authenticated','service_role']) LOOP
  denied := false;
  BEGIN
   EXECUTE format('SET LOCAL ROLE %I',role_name);
   INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method)
     VALUES (1002,90,'test','ci');
  EXCEPTION WHEN insufficient_privilege THEN denied := true;
  END;
  EXECUTE 'RESET ROLE';
  IF NOT denied THEN RAISE EXCEPTION 'FAIL: % INSERT not rejected',role_name; END IF;
 END LOOP;
END $$;
-- Add a SELECT-only policy scoped to authenticated: row visibility is filtered.
CREATE POLICY g3_test_select_authenticated ON intelligence.corporate_actions
 FOR SELECT TO authenticated USING (item_id=1001);
DO $$
DECLARE n bigint;
BEGIN
 EXECUTE 'SET LOCAL ROLE authenticated';
 SELECT count(*) INTO n FROM intelligence.corporate_actions;
 EXECUTE 'RESET ROLE';
 IF n <> 1 THEN RAISE EXCEPTION 'FAIL: SELECT policy expected 1 row, got %',n; END IF;
 EXECUTE 'SET LOCAL ROLE anon';
 SELECT count(*) INTO n FROM intelligence.corporate_actions;
 EXECUTE 'RESET ROLE';
 IF n <> 0 THEN RAISE EXCEPTION 'FAIL: anon policy isolation'; END IF;
 EXECUTE 'SET LOCAL ROLE service_role';
 SELECT count(*) INTO n FROM intelligence.corporate_actions;
 EXECUTE 'RESET ROLE';
 IF n <> 0 THEN RAISE EXCEPTION 'FAIL: service_role policy isolation'; END IF;
END $$;
-- Add a second row and confirm row filter.
INSERT INTO intelligence.corporate_actions(item_id,action_ordinal,action_type,extraction_method)
 VALUES (1002,1,'test','ci');
DO $$
DECLARE n bigint;
BEGIN
 EXECUTE 'SET LOCAL ROLE authenticated';
 SELECT count(*) INTO n FROM intelligence.corporate_actions;
 EXECUTE 'RESET ROLE';
 IF n <> 1 THEN RAISE EXCEPTION 'FAIL: policy filter leaked other item'; END IF;
END $$;
-- Cleanup temporary permissions/policy and prove deny-by-default restored.
DROP POLICY g3_test_select_authenticated ON intelligence.corporate_actions;
REVOKE ALL ON intelligence.corporate_actions FROM anon,authenticated,service_role;
REVOKE ALL ON SEQUENCE intelligence.corporate_actions_id_seq FROM anon,authenticated,service_role;
REVOKE USAGE ON SCHEMA intelligence FROM anon,authenticated,service_role;
DO $$
BEGIN
 IF EXISTS (SELECT FROM pg_policies WHERE schemaname='intelligence' AND tablename='corporate_actions') THEN
  RAISE EXCEPTION 'FAIL: test policy persisted';
 END IF;
 IF has_table_privilege('authenticated','intelligence.corporate_actions','SELECT') THEN
  RAISE EXCEPTION 'FAIL: grants persisted';
 END IF;
END $$;
SELECT 'PASS: FORCE RLS with temporary grants, role isolation, row policy, cleanup' AS result;
