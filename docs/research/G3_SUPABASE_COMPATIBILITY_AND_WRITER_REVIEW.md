# BOHUN G3 | Supabase compatibility and BORME writer review

Date: 2026-10-22. Scope: read-only production metadata inspection plus isolated PostgreSQL 17 CI.

## Confirmed production facts
- Supabase project: `bohundefence-intelligence` (`tecrucawzukxqtchpxrh`).
- `intelligence.items` exists; its PK is `id`, unique key `(source_id, external_id)`; referenced by G3 migration.
- `intelligence.corporate_actions` was absent at prior production preflight. **No G3 migration has been deployed.**
- Existing intelligence tables use RLS enabled, generally without FORCE RLS. The proposed corporate_actions table uses both.
- `anon` and `authenticated` are NOLOGIN, without BYPASSRLS.
- `service_role` is NOLOGIN **with BYPASSRLS**; `postgres` also has BYPASSRLS. Do not assume FORCE RLS constrains either role.
- The current intelligence schema has no direct USAGE grants for anon/authenticated/service_role in prior audit.
- Two SECURITY DEFINER functions in public, `export_inoreader_source_wire` and `rls_auto_enable`, have EXECUTE privileges for anon/authenticated. Their integration and reachability need review before production rollout; do not revoke blindly.
- Data API exposed-schema settings were not verified from dashboard. No production DDL, grants, policies or writes performed.

## Proposed writer model (test-only)
- `bohun_borme_writer NOLOGIN NOBYPASSRLS`.
- USAGE on `intelligence`; SELECT on `intelligence.items` for FK verification; SELECT/INSERT/UPDATE on `intelligence.corporate_actions`; USAGE on its identity sequence.
- No DELETE or schema DDL privileges.
- SELECT policy excludes rejected records; INSERT only unreviewed; UPDATE from unreviewed to unreviewed/reviewed.
- This is **a candidate workflow, not an approved business rule**. Actual ingestion may require idempotent reprocessing and a separate reviewer role; the policy must be revised against real BORME pipeline requirements.
- Important: `intelligence.items` currently has RLS enabled and no writer SELECT policy. A table GRANT alone cannot make it readable. A separate, narrowly scoped policy or trusted ingestion interface is needed. The synthetic fixture does not reproduce this production RLS.
- NOLOGIN role requires a controlled identity/SET ROLE mechanism. Granting membership to a BYPASSRLS role such as service_role undermines intended isolation.

## CI evidence
- Baseline: https://github.com/nab0k/bohun-the-field/actions/runs/39851361875 PASS.
- Expanded RLS: https://github.com/nab0k/bohun-the-field/actions/runs/39852884863 PASS.
- Dedicated writer CI: https://github.com/nab0k/bohun-the-field/actions/runs/39853810223 (status to be updated after completion).
- Test source: `tests/g3/borme_writer_tests.sql`.
- All CI tests use synthetic data; they do not exercise production auth JWT, PostgREST schema exposure, existing RPCs, full production migration state or real BORME ingestion.

## Production readiness gates
1. Confirm the Data API exposed schemas in dashboard.
2. Confirm existing RLS/policies on `intelligence.items`, `intelligence.sources` and writer interactions.
3. Resolve dedicated non-bypass login and identity, or choose tightly scoped backend-only ingestion function.
4. Define editorial review and allowed status transitions; add corresponding positive and negative tests.
5. Reconstruct relevant production schema and migration history in disposable environment.
6. Review public SECURITY DEFINER function attack surface without disrupting current headline export.
7. Run Supabase security advisors and evaluate findings in context.
8. Review migration execution order, rollback strategy and privilege/sequence behavior.

**Decision:** No production approval. This is an evidence-based compatibility review with remaining gates.
