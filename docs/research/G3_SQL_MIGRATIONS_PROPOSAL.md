# BOHUN G3: SQL migrations proposal (NOT executable / NOT applied)

Status: **design review only**. G2.5 completed; depends on approval of G2 logical model PR #89 and database inspection before executable migration creation. Do not apply to Supabase, do not merge as a production migration.

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
