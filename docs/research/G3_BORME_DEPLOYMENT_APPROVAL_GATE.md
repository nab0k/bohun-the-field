# BOHUN G3 / Deployment approval gate

Status: **CI PASS. REVIEW ONLY. NOT APPROVED OR DEPLOYED.**

## CI evidence
- PostgreSQL 17 workflow run **#39861378161**, run number 50: **PASS**.
- URL: https://github.com/nab0k/bohun-the-field/actions/runs/39861378161
- Verified: G3 schema, dedicated writer tests, complete G3 plus source-3 writer migration in a fresh disposable database, direct role source-scoped RLS, PASS report.
- Caveat: this is synthetic PostgreSQL CI, not a full production clone or a real credential connection.


## Verified production state (2026-10-22)
- BORME exists in intelligence.sources: **id=3**, country ES, active.
- No BORME intelligence.items yet at last read-only check.
- intelligence.corporate_actions not deployed as of previous read-only inspection.
- The production writer login and source-scoped policies are not deployed.

## Proposed execution order
1. Verify backup/restore plan, exposed API schemas, existing policies, role grants, and SECURITY DEFINER functions.
2. Recheck source 3 identity, no conflicting policies/roles, table absence.
3. Apply `docs/sql/G3_BORME_CORPORATE_ACTIONS_REVIEW.sql` (G3 table, FORCE RLS, deny by default).
4. Apply `docs/sql/G3_BORME_WRITER_RLS_SOURCE3_REVIEW.sql` (NOLOGIN, least-privilege grants, four RLS policies).
5. Verify policies, RLS, grants, no DELETE and no public/service_role access; check existing ingestion functions for unintended bypass.
6. Provision login and credentials in a **separate** controlled step, after explicit approval; store secret outside Git.
7. Enable BORME ingestion only after integration testing and observability review.

## Important limitations / open gates
- CI uses synthetic fixture on PostgreSQL 17, not a full clone of production.
- Production `intelligence.items` RLS must be enabled and its existing policies audited before applying source policy.
- The role in the reviewed migration is intentionally **NOLOGIN**: it cannot connect yet.
- Cross-source isolation must be retested on a real non-bypass connection after provisioning.
- The writer policy allows updating `unreviewed` actions to `reviewed`. Consider separating ingest and reviewer roles before production rollout.
- The `service_role` and postgres roles bypass RLS. This design requires a dedicated non-bypass database role and prohibits service_role credentials for ingestion.
- Full disposable CI is PASS; a production preflight and explicit authorization are still required.

## Final read-only production preflight (2026-10-22)
- Source registry: TED id=1, Inoreader BOHUN id=2, BORME id=3 (active).
- intelligence.items exists with RLS enabled, FORCE RLS disabled, and no policies.
- intelligence.corporate_actions does not exist.
- bohun_borme_ingest_login does not exist.
- service_role has BYPASSRLS; it must not be used for this ingestion.
- This preflight is metadata only and does not constitute a backup, restore test, or exposure audit.

## Pre-deployment backup and Data API audit (2026-10-22)
**Gate status: BLOCKED pending external verification.**
- Project `bohundefence-intelligence` is ACTIVE_HEALTHY, PostgreSQL 17.11.
- PostgreSQL reports `archive_mode=on` and `wal_level=logical`. **This does not prove a recoverable backup or PITR availability.**
- Backup inventory, latest successful backup timestamp, retention, restore/PITR eligibility and recovery drill: **NOT VERIFIED**. Verify in Supabase Dashboard Database > Backups; capture evidence before deployment.
- `intelligence` schema USAGE for anon/authenticated/service_role: **false**. No direct table grants for anon/authenticated/service_role on `intelligence.items` or `intelligence.sources` found.
- Data API exposed schemas setting: **NOT VERIFIED**. Check Dashboard Integrations > Data API > Settings; ensure `intelligence` is not exposed unless explicitly designed.
- `public.export_inoreader_source_wire` and `public.rls_auto_enable` are SECURITY DEFINER: review EXECUTE privileges, bodies and impact before deployment.
- Supabase security advisor reports multiple RLS-enabled/no-policy findings across the project; assess relevance separately, do not broaden G3 grants to resolve them.
- Never treat direct REST access denial as a sufficient exposure test if privileged RPCs can bypass RLS.

**Release decision:** HOLD until backup evidence, Data API settings, privileged RPC review, and explicit user approval are recorded. No production DDL or credentials provisioning performed.

## RPC security review (2026-10-22, read-only)
- `public.export_inoreader_source_wire()`: SECURITY DEFINER owned by postgres, `search_path=pg_catalog`; EXECUTE explicitly granted to anon, authenticated, service_role. Reads up to 100 most recent unique intelligence.items by URL and returns title, URL, published_at, without source filter. This is a privileged public data disclosure surface; evaluate whether intended before opening G3 or claiming intelligence data private. No production changes.
- `public.rls_auto_enable()`: SECURITY DEFINER owned by postgres, `search_path=pg_catalog`, returns event_trigger. Default PUBLIC EXECUTE. Active event trigger `ensure_rls` on ddl_command_end. Body applies to schema `public` only, not intelligence; no direct G3 impact. Recommend explicit privilege review; do not assume callable as REST RPC.
- Exposure through REST endpoints, exposed-schema settings, and actual unauthenticated RPC response were **not verified**. The function ACL is sufficient to establish database-level privilege, not actual HTTP accessibility.

## Explicit approval required
Approve **only** step 3 + 4 as a single gated deployment after a confirmed PASS and preflight checks; do not provision credentials or enable ingestion automatically.
