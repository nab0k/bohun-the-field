# BOHUN G3 / Deployment approval gate

Status: **PREVIOUS CI PASS; CHANGED SQL NEEDS NEW CI. REVIEW ONLY. NOT APPROVED OR DEPLOYED.**

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

## Approval request (prepared 2026-10-22; execution NOT authorized)
**Requested scope, only after closing preflight gates:**
1. Apply `docs/sql/G3_BORME_CORPORATE_ACTIONS_REVIEW.sql` to production (new `intelligence.corporate_actions`, FORCE RLS).
2. Apply `docs/sql/G3_BORME_WRITER_RLS_SOURCE3_REVIEW.sql` to production (dedicated NOLOGIN role, source_id=3 policies and scoped grants).
3. Perform read-only post-deploy verification of RLS, role, grants and table metadata.

**Excluded:** role LOGIN/password, secret provisioning, ingest activation, BORME data inserts, policy changes outside G3, PR merge, and deployment of any unrelated migrations.

**Evidence:** GitHub Actions #39861378161 PASS on PostgreSQL 17; read-only production audit confirmed BORME id=3 and no pre-existing G3 table/role/policies. RPC privilege audit documented above.

**Hard blockers:**
- Record latest successful backup timestamp, retention and tested/available restore procedure in Supabase Dashboard > Database > Backups.
- Record exact exposed schemas in Dashboard > Integrations > Data API > Settings, and verify that `intelligence` exposure is intentional or absent.
- Re-run schema preflight immediately before applying.
- Obtain explicit authorization after the two confirmations, not merely approval of this document.

**Decision:** HOLD. User confirmation required after evidence is provided. No production DDL executed.

## User-provided dashboard evidence (2026-10-23 local screenshot)
- Supabase Database > Backups screenshot: project is on **Free**; dashboard explicitly states Free Plan does not include project backups. No scheduled backup is available.
- Project overview screenshot: **Last Backup: No backups**; project status Healthy.
- Therefore backup gate is **FAILED**, not merely unverified. Do not apply G3 to production until an independently created, restorable logical backup is verified, or supported backup service is enabled and restore readiness established.
- Second screenshot is project overview, **not** Integrations > Data API > Settings; exposed-schema configuration remains **UNVERIFIED**.
- Current release decision: **HOLD, NOT APPROVED**. No production changes made.

## Data API dashboard evidence (2026-10-23)
- User screenshot of Integrations > Data API > Settings shows **2 of 8 exposed schemas**, selected **public** and **graphql_public**. **intelligence is not exposed**. Data API schema exposure gate: **PASS**.
- `public` is exposed and `public.export_inoreader_source_wire()` has EXECUTE for anon/authenticated; evaluate separately as existing privileged RPC exposure.
- Backup gate remains **FAILED / unverified restore**: Free plan has no scheduled backups, and no independently verified restore artifact has been supplied.
- Deployment remains **HOLD** until verified backup and explicit approval.

## Backup restore drill and production count comparison (2026-10-23; user terminal evidence)
- User created custom-format PostgreSQL 17 dump at `~/BOHUN-backups/bohun_pre_g3_2026-10-23.dump`, reported 2.4 MB, using production Session Pooler.
- Local PostgreSQL 17 database `bohun_g3_restore_test`: first `--schema=intelligence` restore failed because schema did not exist; after explicitly creating schema, second restore exited **0** and restored tables, constraints, indexes, RLS.
- Restored counts and production read-only counts match: sources **3/3**, items **5296/5296**, organizations **0/0**, opportunities **217/217**.
- **PASS: application schema `intelligence` restore drill and four-table row-count comparison.** Not a complete Supabase disaster-recovery test, not row-by-row checksums, and does not prove encrypted off-device retention. Dashboard managed backups remain unavailable on Free.
- Data API exposure check: `public` and `graphql_public` exposed; `intelligence` not exposed: **PASS**.

## Security remediation decision and outstanding RPC gate (2026-10-23)
- Updated `docs/sql/G3_BORME_WRITER_RLS_SOURCE3_REVIEW.sql` to **append-only** ingestion: `SELECT, INSERT` grants only, no UPDATE policy, no DELETE. This prevents loader-driven `unreviewed` → `reviewed` promotion and audit/evidence rewrites. Reviewer functionality needs separate explicitly approved design and migration. This change **invalidates the previous full-CI PASS for the current SQL** until rerun.
- `public.export_inoreader_source_wire()` remains SECURITY DEFINER and EXECUTE-granted to `anon` and `authenticated`, with privileged access to `intelligence.items`. Publishing `public` through Data API means `intelligence` being unexposed is **not** sufficient to dismiss RPC risk. Actual HTTP accessibility and intended public contract remain unverified. **Do not claim RPC remediation completed.**
- Recommended independent security change, subject to explicit approval and regression review: revoke EXECUTE on this RPC from `PUBLIC`, `anon`, `authenticated` (and reassess `service_role`), or replace with authenticated, source-scoped, least-privilege interface if required. No production grants changed.
- `public.rls_auto_enable()` is an event-trigger function affecting new `public` tables; its default PUBLIC EXECUTE deserves hardening review but direct REST callability is unverified. Do not conflate it with confirmed data disclosure.
- Before final approval: rerun CI against revised append-only SQL; confirm current ingestion/test code does not require UPDATE; assess/mitigate privileged RPC; verify off-device backup custody; re-run production preflight.

## Revised decision
**HOLD: no production migrations, credentials, or ingestion activation authorized.** Request final explicit authorization for the two G3 migrations only after updated CI and RPC security gate are closed. Previous text above describing four policies or allowing UPDATE is superseded by this section (current migration has three policies).

## Updated CI and RPC dependency investigation (2026-10-23)
- GitHub Actions PostgreSQL 17 run **#39919850608** (`https://github.com/nab0k/bohun-the-field/actions/runs/39919850608`) completed **SUCCESS** at commit `246e569b25fc403b15028b012f0c820a0dbab3b0` (PR merge checkout). All sql-rls steps succeeded, including three-policy append-only migration, direct role RLS, cross-source INSERT denial, UPDATE privilege denial, duplicate migration rejection and uploaded PASS report.
- GitHub code search for exact `export_inoreader_source_wire` and `inoreader_source_wire` returned **no matches**. Inspected frontend `src/main.js`, `src/atlas/main.js`, Inoreader collector `collectors/inoreader/sync.py`, its workflow, and read-only Supabase scripts; no RPC calls identified in those files. Inoreader collector uses PostgreSQL connection directly.
- **Scope limit:** search/indexing may miss files, and external clients, scheduled jobs, database dependencies, API logs and actual HTTP RPC behavior were not exhaustively checked. No claim of zero production consumers. Database connector returned `link_id must identify an eligible linked account` in a previous read-only attempt.
- **RPC decision:** public EXECUTE restriction is recommended but **not yet verified safe to apply**; first inventory production callers/API logs and approve a separate privilege-only change, with regression/rollback. Do not include RPC revoke in the two G3 migrations without separate approval.
- **Release status:** current SQL CI **PASS**, backup application-schema restore **PASS**, Data API schema exposure **PASS**. RPC production usage remains open; G3 production migrations remain **HOLD** pending explicit authorization and final preflight.

## Actual production RPC traffic: verified (2026-10-23)
- Supabase unified `edge_logs` show **six successful HTTP POST 200** calls to `/rest/v1/rpc/export_inoreader_source_wire` within the inspected last-24-hour window (2026-10-22 07:56 through 2026-10-23 04:43 UTC). All identified as `Python-urllib/3.12`, originating from Microsoft cloud infrastructure; all used the same `sb_publishable_...` API-key prefix. This is **confirmed active external usage**, despite no exact-name references found in this GitHub repository. Logs do not establish client ownership or whether the caller has a JWT.
- Read-only production pg_proc check: zero-argument `public.export_inoreader_source_wire()` is postgres-owned SECURITY DEFINER, returns up to 100 recent distinct `intelligence.items` title/url/published_at values, without source filter; `anon`, `authenticated`, and `service_role` all have EXECUTE. Public REST exposure is real, not merely hypothetical.
- **Security decision:** DO NOT immediately REVOKE from anon/authenticated. Active client would likely break. First identify Microsoft-hosted Python caller, migrate it to authenticated service-to-service access, confirm regression test and owner, then separately approve targeted REVOKE from PUBLIC, anon, authenticated (preserve only required service identity) with rollback. A publishable key is not authentication or an authorization boundary.
- Prepared review-only proposed SQL (not deployed): `REVOKE EXECUTE ON FUNCTION public.export_inoreader_source_wire() FROM PUBLIC, anon, authenticated;` Keep `service_role` only if confirmed needed, noting direct service-role keys must never be put in frontend or logs. Test expected denied/allowed roles and caller success after cutover.
- **G3 release decision:** CI PASS and schema restore PASS, but RPC consumer migration and final production authorization remain **HOLD**. RPC privilege change must be independently authorized and sequenced, not silently bundled with G3.
