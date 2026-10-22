-- EXP-009 R7: the same read-only checks as 03_readonly_preflight_R7.sql,
-- as ONE SELECT returning one JSON value (the Supabase SQL Editor shows only
-- the last statement's result). Contains no writes; output contains no secrets.
SELECT jsonb_pretty(jsonb_build_object(
  '01_migrations', (SELECT coalesce(jsonb_agg(jsonb_build_object('version', version, 'name', name) ORDER BY version), '[]')
                    FROM supabase_migrations.schema_migrations WHERE name LIKE 'bohun_uk%'),
  '02_columns', (SELECT coalesce(jsonb_agg(jsonb_build_object('t', table_name, 'c', column_name, 'type', data_type,
                    'null', is_nullable, 'default', column_default, 'identity', is_identity)
                    ORDER BY table_name, ordinal_position), '[]')
                 FROM information_schema.columns WHERE table_schema = 'bohun_uk_intake'),
  '03_constraints', (SELECT coalesce(jsonb_agg(jsonb_build_object('t', c.conrelid::regclass::text, 'name', c.conname,
                    'type', c.contype, 'def', pg_get_constraintdef(c.oid)) ORDER BY 1), '[]')
                 FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace
                 WHERE n.nspname = 'bohun_uk_intake'),
  '04_rls', (SELECT coalesce(jsonb_agg(jsonb_build_object('t', c.relname, 'rls', c.relrowsecurity,
                    'force', c.relforcerowsecurity) ORDER BY c.relname), '[]')
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'bohun_uk_intake' AND c.relkind = 'r'),
  '04_policies', (SELECT coalesce(jsonb_agg(jsonb_build_object('t', tablename, 'name', policyname, 'cmd', cmd,
                    'roles', roles, 'using', qual, 'check', with_check) ORDER BY tablename, policyname), '[]')
                  FROM pg_policies WHERE schemaname = 'bohun_uk_intake'),
  '05_table_grants', (SELECT coalesce(jsonb_agg(jsonb_build_object('who', grantee, 't', table_name,
                    'priv', privilege_type) ORDER BY grantee, table_name, privilege_type), '[]')
                      FROM information_schema.role_table_grants WHERE table_schema = 'bohun_uk_intake'),
  '05_column_grants', (SELECT coalesce(jsonb_agg(jsonb_build_object('who', grantee, 't', table_name,
                    'c', column_name, 'priv', privilege_type) ORDER BY grantee, table_name, privilege_type, column_name), '[]')
                       FROM information_schema.column_privileges WHERE table_schema = 'bohun_uk_intake'
                       AND grantee NOT IN ('postgres', 'supabase_admin')),
  '06_api_roles_must_be_false', (SELECT jsonb_agg(jsonb_build_object('role', r,
                    'schema_usage', has_schema_privilege(r, 'bohun_uk_intake', 'USAGE'),
                    'notices', has_table_privilege(r, 'bohun_uk_intake.notices', 'SELECT,INSERT,UPDATE,DELETE'),
                    'versions', has_table_privilege(r, 'bohun_uk_intake.notice_versions', 'SELECT,INSERT,UPDATE,DELETE'),
                    'receipts', has_table_privilege(r, 'bohun_uk_intake.run_receipts', 'SELECT,INSERT,UPDATE,DELETE'),
                    'checkpoints', has_table_privilege(r, 'bohun_uk_intake.checkpoints', 'SELECT,INSERT,UPDATE,DELETE')))
                    FROM unnest(ARRAY['anon', 'authenticated', 'service_role']) AS r),
  '07_postgrest_config', (SELECT to_jsonb(rolconfig) FROM pg_roles WHERE rolname = 'authenticator'),
  '08_bohun_roles', (SELECT coalesce(jsonb_agg(jsonb_build_object('role', rolname, 'login', rolcanlogin,
                    'super', rolsuper, 'bypassrls', rolbypassrls, 'createrole', rolcreaterole,
                    'createdb', rolcreatedb, 'replication', rolreplication) ORDER BY rolname), '[]')
                     FROM pg_roles WHERE rolname LIKE 'bohun_uk%'),
  '08_memberships', (SELECT coalesce(jsonb_agg(jsonb_build_object('role', m.roleid::regrole::text,
                    'member', m.member::regrole::text)), '[]')
                     FROM pg_auth_members m WHERE m.roleid::regrole::text LIKE 'bohun_uk%'
                        OR m.member::regrole::text LIKE 'bohun_uk%'),
  '09_writer_outside_schema_must_be_false', jsonb_build_object(
      'items', has_table_privilege('bohun_uk_ingest_writer', 'intelligence.items', 'SELECT,INSERT,UPDATE,DELETE'),
      'intelligence_schema', has_schema_privilege('bohun_uk_ingest_writer', 'intelligence', 'USAGE'),
      'exp009_schema', CASE WHEN to_regnamespace('exp009') IS NULL THEN NULL
                            ELSE has_schema_privilege('bohun_uk_ingest_writer', 'exp009', 'USAGE') END),
  '10_public_table_grants', (SELECT coalesce(jsonb_agg(jsonb_build_object('s', table_schema, 't', table_name,
                    'priv', privilege_type) ORDER BY table_schema, table_name), '[]')
                    FROM information_schema.role_table_grants
                    WHERE grantee = 'PUBLIC' AND table_schema NOT IN ('pg_catalog', 'information_schema')),
  '10_public_secdef_functions', (SELECT coalesce(jsonb_agg(n.nspname || '.' || p.proname ORDER BY 1), '[]')
                    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE p.prosecdef AND has_function_privilege('public', p.oid, 'EXECUTE')
                    AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'extensions', 'graphql',
                        'graphql_public', 'pgbouncer', 'realtime', 'storage', 'vault', 'auth')),
  '11_counts_must_be_0', jsonb_build_object(
      'notices', (SELECT count(*) FROM bohun_uk_intake.notices),
      'versions', (SELECT count(*) FROM bohun_uk_intake.notice_versions),
      'receipts', (SELECT count(*) FROM bohun_uk_intake.run_receipts),
      'checkpoints', (SELECT count(*) FROM bohun_uk_intake.checkpoints))
)) AS r7_preflight;
