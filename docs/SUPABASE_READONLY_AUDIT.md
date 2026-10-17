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

## Verified result — 2026-10-13

GitHub Actions run [38360422753](https://github.com/nab0k/bohun-the-field/actions/runs/38360422753)
completed successfully against the actual Supabase `intelligence` schema. The
redacted artifact is named `supabase-intelligence-schema-audit`.

- Ten base tables exist: `events`, `item_changes`, `item_locations`,
  `item_organizations`, `items`, `locations`, `opportunities`, `organizations`,
  `signals`, and `sources`. There are no views.
- RLS is enabled but not forced on every table. No RLS policies were returned.
- The connected `postgres` role has all table privileges; the audit did not find a
  least-privilege read-only API role.
- The provenance join `items.source_id → sources.id` exists. Item/event geography
  can join through `events.location_id → locations.id` or `item_locations`.
- `items` supplies `title`, `url`, `published_at`, and `summary`; locations/sources
  supply `country_code`.
- No relation supplies `content_hash`, `public_release`, a QA decision, or an
  approved-only projection. The only status-like field is `opportunities.status`;
  all 167 rows fall outside APPROVE/HOLD/REJECT/PUBLISHED buckets.

Therefore the number of records demonstrably allowed for public release is **zero**.
There is no exact source view or QA predicate that issue #55 can safely query. The
PR #53 browser adapter must continue to show its honest empty state.

Unblocking #55 requires owner-authorized database work outside this PR: create a
reviewed approved-only projection containing immutable source provenance and an
explicit public-release decision, then provide Cloudflare with a SELECT-only
credential limited to that projection. No current table should be exposed directly.
