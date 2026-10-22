# EXP-009 stage 1: UK FTS tender history and useful fields (bohun_uk_intake)

DRAFT ONLY. DO NOT MERGE, DEPLOY, SCHEDULE OR PUBLISH WITHOUT SEPARATE OWNER APPROVAL.

Stacked on R7.1 in this PR. Still writes only to the private schema `bohun_uk_intake`; never to `intelligence.items`; checkpoints untouched; all rows HOLD / UNVERIFIED / `public_release=false`.

## What changes
- **One notice per `ocid`; one append-only version per source release.** `tender` and `tenderUpdate` releases are collected (award/planning stay out).
- **Sanitized release stored in each version**: `parties[].contactPoint` (names, e-mails, phones of people) is removed before storage and hashing; the DB rejects any version that still contains it. Package licence (OGL v3) and publisher are stored with each version.
- **Overlay rule**: a release changes only the fields it actually contains, so partial updates do not erase value, deadline or status. Releases are applied in (date, release id) order; an older release arriving later is stored as a version but does not change the current state.
- **Fields**: description, status, procurement method, category, value/gross/currency, deadline (`tender_deadline` only when the source time has an offset; `tender_deadline_raw` always keeps the source string, e.g. date-only `2026-10-26`), buyer id/name/country, CPV codes (from `items[].additionalClassifications`), lots count, above-threshold flag, FTS notice URL (only `https://www.find-tender.service.gov.uk/Notice/...` from `documents[tenderNotice]`), completeness (`HAS_TENDER_NOTICE` / `UPDATE_ONLY`).

## Files
- `uk_fields.py`: sanitize + extract. `uk_window.py`: window selection, ordering, licence/publisher. `uk_stage_writer_v2.py`: writer (dry run by default, `--commit` to persist; same R7.1 guards: UK-local window, minimal-privilege preflight, one transaction, advisory lock).
- `tests/test_stage1.py`: 25 tests on a stateful fake DB (`RealPageTests` only with `BOHUN_UK_REAL_PAGE`).
- `uk_S1_D8_DELETE_PILOT_ROWS.sql`, `uk_S1_M1_MIGRATION.sql`: **already executed** on 2026-10-22; reference copies, do not rerun. `uk_S1_M1_DOWN.sql`: rollback, not executed. `uk_S1_READONLY_PREFLIGHT.sql`: read-only check.

After M1 the R7.1 writer (`uk_staging_writer.py`) no longer matches the schema; it stays as the source of shared guards and history. `uk_R7_STAGING_SCHEMA_APPLIED.sql` / `uk_R7_WRITER_GRANTS_APPLIED.sql` describe the pre-M1 shape.

## Evidence, 2026-10-22 (each step separately approved by the owner)
1. D8: 10 R7.1 pilot notices removed (receipts kept). M1 applied; read-only preflight passed (new columns, constraints, RLS, writer cannot delete, update versions, change QA/public flags or touch checkpoints; API roles have no access).
2. Dry run on the live DB: 15 releases → 14 notices, 1 applied update, 15 versions; rolled back to 0/0.
3. One commit: 14 notices (10 `HAS_TENDER_NOTICE`, 4 `UPDATE_ONLY`), 15 versions (10 tender, 5 tenderUpdate), 0 contact points, 0 e-mail addresses in stored JSON, one COMPLETE receipt (42/15/14/1/0).
4. Rerun: 0 notices, 0 versions, 15 repeated.
- Offline: 83 tests in `tests/` + 45 normalizer/source/R5 tests pass.

## Not yet proven on the live DB
Late older release (`applied_to_current=false`) and a republished release with a different hash — covered offline only.

## Next (separate approvals)
Stage 2 failed-run receipts; stage 3 checkpoints and gap-free hourly traversal; stage 4 30-day backfill; stage 5 GitHub Actions schedule (public repo: counters only in logs); stage 6 seven-day soak.
