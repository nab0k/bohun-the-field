# Supabase intelligence read-only audit

This audit is the evidence gate for issue #54 and PR #53. `db/schema.sql` is
historical D1/SQLite material and is not used to infer the live schema.

Run the GitHub Actions workflow **Supabase intelligence schema audit (READ ONLY)**
manually. It needs the existing `SUPABASE_DB_PASSWORD` repository secret and may
optionally use `SUPABASE_DB_HOST`, `SUPABASE_DB_PORT`, `SUPABASE_DB_USER`, and
`SUPABASE_DB_NAME` environment overrides. The workflow never prints credentials,
record bodies, titles, URLs, coordinates, or people.

Before the standalone workflow reaches the default branch, use the existing
**M0 Public Release Readiness (NO DEPLOY)** workflow with the
`supabase_schema_audit` input enabled. That input skips its normal release jobs and
runs only the same read-only audit. This bootstrap path is necessary because GitHub
will not dispatch a newly added workflow that is absent from the default branch.

The artifact `supabase-intelligence-schema-audit` contains `report.json` with:

- actual relations, columns and PostgreSQL types in `intelligence`;
- RLS flags, sanitized policies and table-level grants (custom role names are hashed);
- foreign-key metadata needed to prove the source/provenance join chain;
- view definitions with comments and string literals removed;
- coarse `APPROVE`, `HOLD`, `REJECT`, `PUBLISHED`, `OTHER`, and `NULL` counts for
  status-like columns;
- candidate locations for every field expected by PR #53.

The audit opens `BEGIN READ ONLY`, applies short statement and lock timeouts, and
rolls back. A missing credential or database error produces a redacted `BLOCKED`
report and a failing workflow. An operator can reproduce the metadata portion with
`docs/sql/supabase-readonly-audit.sql`.

No API implementation is authorized until the artifact identifies the exact source
relation/view, immutable provenance field, QA decision field, and public-release
predicate. If no single approved projection supplies all of those fields, issue #55
remains blocked; this audit does not authorize a migration.
