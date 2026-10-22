# G2 · Closed company facts / site profile integration · DRAFT

Goal: feed existing `bohundefence.com/company-profile.html` using a real canonical `companies.company.id`, claim-level evidence and a future approved-only export. The existing site renders public snapshots; the G1 site PR #26 already has a localhost-only private review loader.

## Review-only delta

- `db/companies/004_private_profile_claims.sql` proposes FOUR additive private staging tables: source, claim, claim-source, research case. This is deliberately stacked onto PR #80 because migration 003 is already present there. Although the live database reports version 003 applied, this branch does not execute any migration.
- Every claim retains its source IDs and individual assessment. Review state is HOLD. `profile_research_case` holds unresolved people/org/brand identity cases against existing `discovery_candidate.id`. Source event country is not legal country. No new `company` is created.
- Compiler `collectors/companies/private_profile_stage.py` builds a private manifest. `scripts/g2_compile_private_stage.py` accepts THREE private input JSON files and writes outside checkout only.
- Data packages for Accurision and 20 cases are **not** committed to GitHub. They are the owner's local G1 pilot materials, with 23 research claims, 6 source references, 20 case records.
- The owner-local G2 acceptance: 6 sources, 23 claims, 38 claim→source links, 20 research cases; repeat processing is idempotent. SQLite simulation only, NOT a PostgreSQL migration test.
- Existing SITE G1 draft PR #26 is the target private UI renderer; it has no public data access. Private G2 bridge is distributed in the owner-local package.

## Release and action gates

1. Owner reviews exact schema and separately authorizes applying migration 004 to Supabase. Before that, check live migration history/roles and run an isolated PostgreSQL dry-run. Never apply 004 by merely merging a PR.
2. Owner separately authorizes ONE write of the 23 claims and 6 sources for existing Accurision company ID, plus 20 research cases; all HOLD and private. Re-run idempotency and private RLS/privilege queries.
3. Rehydrate the private site view model from the database readback. Browser smoke and quality review are mandatory. No site merge/public release.
4. Only after QA and a separate explicit public release can a sanitized public export feed existing `company.js`. No direct browser Supabase credentials.

No auto-collector, no GitHub Actions schedule, no Notion writes, no automatic identity promotion, no merge/deploy/publication authorized.
