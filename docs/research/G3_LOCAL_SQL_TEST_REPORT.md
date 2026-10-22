# BOHUN G3 | PostgreSQL 17 SQL / RLS test report

**Result: PASS for synthetic CI fixture; NOT production approval.**

- Date (UTC): 2026-10-22
- GitHub Actions run: https://github.com/nab0k/bohun-the-field/actions/runs/39851361875
- Job ID: 120078124286
- Commit tested: `b5b66514919b482ef02c52ec2c797d6c15f1c59f`
- PostgreSQL server: **17.11** (from job container logs)
- Migration: `docs/sql/G3_BORME_CORPORATE_ACTIONS_REVIEW.sql` (unchanged source from main)
- Fixture: `tests/g3/fixture.sql` (synthetic, minimal `intelligence.items`)
- Assertions: `tests/g3/assertions.sql`
- CI workflow: `.github/workflows/bohun-g3-postgres17.yml`

## Evidence and test results

| Test | Status | Evidence |
|---|---|---|
| PostgreSQL 17 service initialized | PASS | CI step `Initialize containers`, server logs PostgreSQL 17.11 |
| Minimal schema and synthetic records | PASS | `Version and fixture` |
| Original migration applied | PASS | `Apply original G3 migration` |
| RLS and FORCE RLS flags | PASS | `Validate constraints and RLS metadata` |
| No RLS policies and no table/sequence grants to anon, authenticated, service_role | PASS | `Validate constraints and RLS metadata` |
| FK, UNIQUE, CHECK, default status, ON DELETE RESTRICT | PASS | `Validate constraints and RLS metadata` |
| SELECT denied for anon, authenticated, service_role | PASS | `Validate role SELECT denial`; error was `permission denied for schema intelligence` |
| Repeated migration rejected | PASS | `Verify migration fails on repeat`, expected `already exists: manual review required` |
| Report artifact uploaded | PASS | `bohun-g3-sql-test-report`, artifact ID `12456249666` |

## Limitations / remaining gates

1. The test schema is deliberately minimal. It does **not** reproduce all production schemas, ownership, triggers, permissions, extensions, or data.
2. Runtime SELECT rejection currently occurs at **schema USAGE** level. It demonstrates effective denial but **does not independently prove row-policy enforcement** if schema/table access is granted later. Additional role-specific RLS tests are required before granting writer access.
3. No INSERT/UPDATE/DELETE execution attempts were made under the restricted roles. Grant metadata checks passed.
4. No production or staging Supabase DDL was executed; the CI database is disposable.
5. Writer role, ingestion grants, policies, API exposed-schema configuration, and SECURITY DEFINER RPC interaction require a separate security review.
6. The workflow writes a run artifact; this checked-in document is a manually captured report from that completed run, not automatically committed by CI.

## Decision

**PASS for initial SQL compatibility and deny-by-default posture on PostgreSQL 17.11.**
**NOT APPROVED FOR PRODUCTION** pending full-schema compatibility, role-specific RLS execution tests, writer policy design, and deployment review.
