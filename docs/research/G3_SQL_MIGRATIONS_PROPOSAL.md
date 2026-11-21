# BOHUN G3: SQL migrations proposal (NOT executable / NOT applied)

Status: **design review only; live Supabase inventory completed 2026-10-22, implementation plan supersedes greenfield SQL sketch below**. G2.5 completed; depends on approval of G2 logical model PR #89 and database inspection before executable migration creation. Do not apply to Supabase, do not merge as a production migration.

## Proposed rollout
1. Migration 001: `source_system`, `ingestion_run`, `organization`, `organization_identifier`.
2. Migration 002: `source_document`, `funding_project`, `project_source_key`.
3. Migration 003: `corporate_event`, `entity_mention`, `source_observation`.
4. Migration 004: add **validated** partial unique indexes, check constraints and RLS policies only after sample and longitudinal validation.

## PostgreSQL sketch (illustrative, intentionally not a runnable migration)

```sql
-- Design sketch only. Use reviewed migration tooling and explicit schema namespace.
CREATE TABLE source_system (
  source_code text PRIMARY KEY,
  jurisdiction text NOT NULL,
  source_url text,
  terms_review_status text NOT NULL DEFAULT 'pending'
);
CREATE TABLE organization (
  organization_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  country_code char(2),
  legal_name text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE organization_identifier (
  identifier_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  organization_id uuid NOT NULL REFERENCES organization(organization_id),
  country_code char(2) NOT NULL,
  scheme text NOT NULL,
  normalized_value text NOT NULL,
  validation_status text NOT NULL DEFAULT 'unverified',
  provenance text NOT NULL
);
-- Unique index on verified identifiers only, after jurisdiction-specific validation.
CREATE TABLE source_document (
  document_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_code text NOT NULL REFERENCES source_system(source_code),
  external_id text,
  issue_date date,
  title text,
  canonical_url text,
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now()
);
-- Do NOT yet create UNIQUE(source_code, external_id):
-- G2.4 validated 37/37 IDs in ONE issue, G2.5 completed: 6/7 issues processed successfully, 194/194 IDs present and distinct, zero within-issue or cross-issue collisions over sampled issues (2024–2026); 2026-05-28 raised ValueError and was excluded. Global uniqueness outside this sample remains unproven.
CREATE TABLE funding_project (
  project_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_code text NOT NULL REFERENCES source_system(source_code),
  organization_id uuid REFERENCES organization(organization_id),
  project_title text NOT NULL,
  decision_date date,
  budget_amount numeric,
  contribution_amount numeric,
  currency_code char(3),
  first_seen_at timestamptz NOT NULL DEFAULT now(),
  last_seen_at timestamptz NOT NULL DEFAULT now()
);
```

## Remaining entities
- `ingestion_run`: run UUID, source FK, source period, started/completed timestamps, status, counts, safe error category.
- `project_source_key`: project FK, source FK, key version and digest, first/last seen; no uniqueness until longitudinal CDTI tests.
- `corporate_event`: event UUID, document FK, type, event date, extraction method and review status.
- `entity_mention`: mention UUID, document FK, optional organization FK, role, confidence and review status.
- `source_observation`: observation UUID, ingestion run FK, entity locator, change type and content digest. Raw payload retention separately approved.

## Safety / acceptance gates
- G2.5 completed across 2024–2026 sampled issues; **blocking follow-up**: diagnose 2026-05-28 ValueError, expand representative coverage, and define how unavailable issues are handled.
- Confirm source identifiers remain stable across repeated snapshots, and source terms permit storage and redistribution.
- Check existing Supabase schema, migration history, schema namespace, extensions, roles, RLS, grants and service access **before** preparing executable SQL.
- Choose exact RLS policies, retention limits, error handling, indices and backfill plan; review rollback for every migration.
- Never expose source identifiers or raw records in Actions logs; secrets only in protected environment.
- No automatic merge of organization entities by legal name, and no silent overwriting of funding project records on title changes.

## Evidence
- CDTI G2.2: 1,069 records, 925 distinct NIF, zero collisions for NIF + title in one snapshot.
- BORME G2.4: 37/37 nonempty and unique publication IDs in 2024-05-29 issue.
- G2.5: **pending**. This design is intentionally provisional.

## G2.5 cross-issue acceptance record (2026-10-22)
- Run: https://github.com/nab0k/bohun-the-field/actions/runs/39842241320 (GitHub job successful; one source date failed).
- 6/7 dates returned parsable issue summaries: 2024-05-28 (28), 2024-05-29 (37), 2024-05-30 (35), 2025-05-28 (34), 2025-05-29 (31), 2026-05-29 (29).
- 194 total publication items, all 194 had IDs, all 194 distinct; 15 comparisons between successful issues yielded zero overlap.
- 2026-05-28: ValueError reported; not counted as an empty issue. Root cause unknown from aggregate report.
- **Design decision**: keep surrogate UUID `source_document.document_id`; treat `(source_code, external_id)` as preferred natural key for BORME matching, but do not apply unique index until representative further testing and inspection of existing records.
- On conflicts or missing ID, quarantine and review; do not automatically merge documents on title or URL alone.
- The unique index is a separately approved migration gate and not part of the baseline schema migration.

## Live Supabase schema / RLS audit (2026-10-22, READ ONLY)

Project: `bohundefence-intelligence` (`tecrucawzukxqtchpxrh`), PostgreSQL 17.11.0.003, ACTIVE_HEALTHY.

**CRITICAL: Existing schema is not empty. Do not apply the greenfield `CREATE TABLE` sketch above.** Nine Supabase migration records exist, including `companies_002_discovery_intake`, `companies_003_encrypted_evidence_archive`, and `create_private_source_registry`. `public` has no tables in the list_tables inventory, but domain tables live in private/custom schemas.

Verified relevant tables:
- `intelligence.sources`: id bigint, name, url, source_type, country_code, collection_method, active.
- `intelligence.items`: id bigint, source_id bigint, external_id, title, url, item_type, published_at, raw_data jsonb, first_seen_at, last_seen_at.
- `intelligence.organizations`: id bigint, name, website, country_code, organization_type.
- `intelligence.events`: id bigint, item_id bigint, starts_at, ends_at, location_id. **This is a time/location event; do not assume it already models BORME corporate events.**
- `intelligence.opportunities`: item_id, opportunity_type, amount, currency_code, deadline, buyer_organization_id.
- `intelligence.item_changes`: item_id, source_change_id, detected_at, old_data, new_data.
- `companies.company`: id text, name, name_norm, country, website, status, flags.
- `companies.company_identifier`: scheme, value, company_id, source, observed_at.
- `companies.run`, `companies.observation`, `companies.source_state` support ingestion and provenance.
- `bohun_private.sources`: separate source registry.
- `bohun_staging.raw_documents`: document_id text, source_id, external_id, content_sha256, media_type, original_url, raw_content.
- `bohun_staging.inoreader_documents`: Inoreader-specific staged content, rights_status, qa_status, public_release.
- `bohun_uk_intake.notices`: procurement notices, external_id, ocid, release data.
- `exp009.runtime_documents`: experiment-specific document JSON.

### Revised migration strategy (supersedes greenfield phases)
0. **No writes** until schema ownership, data API exposed schemas, existing PK/FK/unique constraints, row counts, and grant/policy matrix are audited.
1. Extend existing `intelligence.sources` / `intelligence.items` for BORME source + publication metadata only if existing constraints and ownership permit. Prefer `(source_id, external_id)` after a duplicate preflight; do not create a global index yet.
2. Reuse `companies.company_identifier` for NIF and ICO with country-aware validation; map to `companies.company` via a reviewed bridge to `intelligence.organizations` rather than creating another company master.
3. Design a distinct CDTI funding project model only if `intelligence.opportunities` cannot represent grant awards safely. Do not conflate awarded grants with open opportunities.
4. Design a separate corporate-event extraction table if `intelligence.events` cannot represent document-derived corporate actions.
5. Add observation/source provenance and idempotent upsert contracts by extending existing mechanisms; no destructive refactoring.
6. Security review, staging migration, backfill simulation, rollback, then explicitly approved production migration.

### RLS observations
- `intelligence` domain tables inspected have RLS enabled, zero policies: likely intentional deny-by-default for ordinary roles, not automatically a vulnerability.
- `companies.company` has RLS enabled with one policy; related `companies.company_identifier` and many other tables have zero policies.
- `bohun_staging.company_candidates` and `harvest_candidates` have FORCE RLS and zero policies.
- `bohun_staging.raw_documents` and `inoreader_documents`, `bohun_private.sources`, and `bohun_staging.contract_packets` have RLS disabled: **check schema exposure and role grants before assessing risk**.
- Security advisor reported 38 `rls_enabled_no_policy` notices (INFO). These are not evidence of public access.
- Check views, SECURITY DEFINER functions, EXECUTE privileges, Data API exposure, and direct grants before enabling new policies.
- **No RLS changes or migrations executed during audit.**

## Live PK/FK/UNIQUE and RLS access audit (2026-10-22, read-only)

Verified directly from `pg_constraint`, `pg_policies`, `has_schema_privilege` and `has_table_privilege`:

- `intelligence.items` already has `UNIQUE(source_id, external_id)` and FK to `intelligence.sources(id)`. **Do not add a duplicate unique constraint**. BORME ingestion can use this existing key with source-scoped IDs after preflight.
- `bohun_staging.raw_documents` has PK `document_id`, `UNIQUE(source_id, external_id, content_sha256)`, and SHA256-format CHECK.
- `companies.company_identifier` has PK `(scheme, value)` and FK `company_id` to `companies.company(id)` ON DELETE CASCADE. Namespace NIF/ICO schemes explicitly, and avoid altering existing primary key without migration impact analysis.
- `intelligence.events` has `UNIQUE(item_id)`; cannot represent many corporate actions per BORME publication.
- `intelligence.opportunities` has `UNIQUE(item_id)`; cannot represent multiple independently addressable funding awards per publication without a separate entity or reviewed remodel.
- `companies.company` has PK text `id` and `UNIQUE(notion_page_id)`. No UUID migration is justified without a cross-schema identity plan.

Access: for `anon`, `authenticated`, `service_role`, `USAGE` was false for `intelligence`, `companies`, `bohun_staging`, `bohun_private`, `bohun_uk_intake`, `exp009`; `public` had USAGE true. All inspected `intelligence`, `companies`, `bohun_staging`, `bohun_private` tables had SELECT false for those three roles. This is **not** a complete Data API exposure audit: check configured exposed schemas and views/RPCs separately.

Existing RLS policies: `companies.company` has SELECT for `exp009_owner` limited to non-excluded companies; `bohun_uk_intake.notices` has 3 writer policies restricted to HOLD/UNVERIFIED, `notice_versions` has 2, `run_receipts` has 2. Preserve role-specific gates. 38 zero-policy RLS tables are not automatically a security vulnerability; do not blanket-add permissive policies.

### Targeted migration plan (non-executable pending further review)
- **M1**: no DDL for `intelligence.items` uniqueness; introduce BORME/CDTI/ARES source registrations through reviewed data-seeding, plus ingestion contracts. Existing source rows must be checked first.
- **M2**: separate `intelligence.corporate_actions` (one publication to many actions) with FK to `intelligence.items`, type, effective date, extraction/review status; use RLS enabled and no public grants. Only after assessing schema ownership and existing views.
- **M3**: separate CDTI award/project relation if `intelligence.opportunities` does not semantically fit awarded projects; versioned source-key and link to existing company identity. Do not use project title alone as key.
- **M4**: optional reviewed crosswalk between `companies.company` and `intelligence.organizations`; preserve text and bigint PKs.
- **M5**: role-specific ingestion grants/policies only after confirming the intended writer role and whether the schema is exposed through PostgREST. Test denial for anon/authenticated and scoped writes for writer.

**Pending gates**: API exposed-schema configuration, views/RPC/SECURITY DEFINER audit, index inventory, duplicate preflight and row counts, schema owners, integration writer role, rollback and staging tests. No DDL applied.
