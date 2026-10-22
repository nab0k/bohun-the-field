# BOHUN G3 / Deployment approval gate

Status: **REVIEW ONLY. NOT APPROVED OR DEPLOYED.**

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
- The new complete CI run must be PASS before any production approval.

## Explicit approval required
Approve **only** step 3 + 4 as a single gated deployment after a confirmed PASS and preflight checks; do not provision credentials or enable ingestion automatically.
