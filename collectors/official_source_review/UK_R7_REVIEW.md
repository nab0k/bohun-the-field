# EXP-009 R7.1: UK Find a Tender HOLD staging writer (bohun_uk_intake)

DRAFT ONLY. DO NOT MERGE, DEPLOY, SCHEDULE OR PUBLISH WITHOUT SEPARATE OWNER APPROVAL.

Stacked on R6 (this PR). Writes only to the private Supabase schema `bohun_uk_intake`, never to `intelligence.items`. R6 `uk_hold_apply.py` (which targets `intelligence.items`) is unchanged and must not be used for this pilot.

## What R7.1 changes relative to the R7 handoff package
- `source_window_utc()`: the UK API reads `updatedFrom/updatedTo` without offset as UK local time (observed: all 42 releases of 2026-10-15 09:00–10:00 dated +01:00). Receipt bounds are converted from Europe/London; windows touching a DST change are refused.
- Every candidate's `published_at` must lie inside the receipt window, else stop before the database.
- `_preflight`: session must not be superuser/bypassrls/createrole/createdb, must have no access to `intelligence.items`, `exp009` or `checkpoints`, must have column-level INSERT on `notices` and membership in `bohun_uk_ingest_writer`. Uses `has_any_column_privilege` (grants are column-level) and a `pg_class` lookup for `intelligence.items` (`to_regclass` fails without schema USAGE).
- `dry_run=True` by default: all statements run under the real role, grants, RLS and constraints, then the transaction is rolled back. Commit only with `--commit`.
- `--check-only`: login + privilege check in a READ ONLY transaction, no source read.
- DSN must not contain a password; libpq takes it from `PGPASSWORD`, which the operator reads from the Keychain at launch.

## Evidence (2026-10-22, each step separately approved by the owner)
1. Read-only preflight of the live schema: columns, constraints, 7 RLS policies and grants match `uk_R7_STAGING_SCHEMA_APPLIED.sql` / `uk_R7_WRITER_GRANTS_APPLIED.sql`; anon/authenticated/service_role have no access; tables empty.
2. Login role `bohun_uk_ingest_login` (member of the NOLOGIN writer only) created; `--check-only` → privileges minimal.
3. Dry run on the live DB: 10 inserted, rolled back, tables 0/0/0.
4. One commit: 10 notices HOLD/UNVERIFIED/public_release=false, 1 COMPLETE receipt (42/10/10/0/0), checkpoints untouched.
5. Rerun of the same window: 0 inserted / 10 repeated, still 10 notices, 0 versions, 2 receipts.

Not yet proven on the live DB: the UPDATE + `notice_versions` path (needs a source record that really changed).

## Test
From collectors/official_source_review:
    python -m unittest discover -s tests -v
`RealPageTests` run only with `BOHUN_UK_REAL_PAGE=<saved read-only page>`; no source data is committed.

## Open items before any schedule
Versions keyed by ocid (tenderUpdate releases are separate rows today); description/value/deadline/buyer not stored; failed runs leave no DB receipt (RLS allows COMPLETE only); checkpoints and gap-free window traversal; PUBLIC-executable `public.rls_auto_enable` type not yet checked.
