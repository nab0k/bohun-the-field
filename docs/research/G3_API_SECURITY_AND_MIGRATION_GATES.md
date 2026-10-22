# G3 BORME migration review and API security gates

Status: **executable SQL prepared, NOT deployed or tested against a database**.

## Context
- Live inventory previously confirmed `intelligence.items(id bigint)`, `UNIQUE(source_id, external_id)`, and `intelligence.events UNIQUE(item_id)`.
- One BORME publication can yield many corporate actions, so a separate one-to-many table is appropriate.
- No change to existing `intelligence.items`, `intelligence.events`, `companies.company`, or `companies.company_identifier`.
- The executable DDL is `docs/sql/G3_BORME_CORPORATE_ACTIONS_REVIEW.sql`. It is not installed under an auto-deploy migration directory intentionally.
- SQL creates a table and index, enables FORCE RLS, revokes standard-role privileges, creates **no** access policies and **no** writer grants.
- **Important:** FORCE RLS may affect owner-operated ingestion. Writer access requires a separate reviewed migration and integration tests.
- No claims of complete audit: Supabase connector became unavailable with `link_id must identify an eligible linked account` while checking API exposure, views, functions, owners and row counts.

## Required live read-only queries before deployment

```sql
-- Data API configuration may be outside PostgreSQL; dashboard verification also required.
SELECT name, setting FROM pg_settings WHERE name LIKE 'pgrst.%' ORDER BY name;

SELECT n.nspname AS schema_name, c.relname AS view_name, c.reloptions,
       has_table_privilege('anon', c.oid, 'SELECT') AS anon_select,
       has_table_privilege('authenticated', c.oid, 'SELECT') AS authenticated_select
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind IN ('v','m')
  AND n.nspname IN ('public','intelligence','companies','bohun_staging','bohun_private','bohun_uk_intake','exp009');

SELECT n.nspname AS schema_name, p.proname, p.prosecdef AS security_definer,
       pg_get_function_identity_arguments(p.oid) AS arguments,
       has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_execute,
       has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authenticated_execute
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname IN ('public','intelligence','companies','bohun_staging','bohun_private','bohun_uk_intake','exp009');

SELECT n.nspname AS schema_name, pg_get_userbyid(n.nspowner) AS owner
FROM pg_namespace n WHERE n.nspname IN ('public','intelligence','companies','bohun_staging','bohun_private','bohun_uk_intake','exp009');

SELECT 'intelligence.items' AS relation, count(*) FROM intelligence.items
UNION ALL SELECT 'intelligence.sources', count(*) FROM intelligence.sources
UNION ALL SELECT 'companies.company', count(*) FROM companies.company;
```

## Approval gates
1. Restore connected Supabase access and verify API exposed schemas from Dashboard (SQL setting may be absent).
2. Review every callable privileged function and every exposed view, including `public`, and confirm search_path and privileges.
3. Review existing data, table owners, indexes and current grants.
4. Verify the intended ingestion writer role and define narrowly scoped INSERT/SELECT policies in a subsequent PR.
5. Check SQL in disposable staging and verify anon/authenticated cannot read or write, and the intended writer works once authorized.
6. Run migration tooling to generate the canonical migration filename; keep this review SQL out of automated deployment until then.
7. Obtain explicit approval for production deployment; confirm rollback and backups.
