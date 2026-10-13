-- Operator fallback for issue #54. Run against Supabase PostgreSQL with a
-- least-privilege read-only role. It returns metadata and aggregate counts only.
BEGIN READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '2s';

SELECT table_name, column_name, data_type, is_nullable, ordinal_position
FROM information_schema.columns
WHERE table_schema = 'intelligence'
ORDER BY table_name, ordinal_position;

SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity
FROM pg_catalog.pg_class c
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'intelligence' AND c.relkind IN ('r', 'p', 'v', 'm')
ORDER BY c.relname;

SELECT viewname, definition
FROM pg_catalog.pg_views
WHERE schemaname = 'intelligence'
ORDER BY viewname;

SELECT table_name, grantee, privilege_type, is_grantable
FROM information_schema.role_table_grants
WHERE table_schema = 'intelligence'
ORDER BY table_name, privilege_type, grantee;

SELECT tc.constraint_name, tc.table_name, kcu.column_name,
       ccu.table_name AS foreign_table_name,
       ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints tc
JOIN information_schema.key_column_usage kcu
  ON kcu.constraint_name = tc.constraint_name
 AND kcu.constraint_schema = tc.constraint_schema
JOIN information_schema.constraint_column_usage ccu
  ON ccu.constraint_name = tc.constraint_name
 AND ccu.constraint_schema = tc.constraint_schema
WHERE tc.constraint_schema = 'intelligence' AND tc.constraint_type = 'FOREIGN KEY'
ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position;

SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
FROM pg_catalog.pg_policies
WHERE schemaname = 'intelligence'
ORDER BY tablename, policyname;

-- After the metadata query identifies the actual QA/status column, an operator
-- may run this template with identifiers substituted. Do not select row content.
-- SELECT CASE
--   WHEN <status_column> IS NULL THEN 'NULL'
--   WHEN upper(<status_column>::text) IN ('APPROVE', 'APPROVED') THEN 'APPROVE'
--   WHEN upper(<status_column>::text) = 'HOLD' THEN 'HOLD'
--   WHEN upper(<status_column>::text) IN ('REJECT', 'REJECTED') THEN 'REJECT'
--   WHEN upper(<status_column>::text) IN ('PUBLISH', 'PUBLISHED') THEN 'PUBLISHED'
--   ELSE 'OTHER' END AS bucket, count(*)
-- FROM intelligence.<relation> GROUP BY 1 ORDER BY 1;

ROLLBACK;
