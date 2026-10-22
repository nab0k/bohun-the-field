-- BOHUN / EXP-009 / UK Find a Tender
-- APPLIED as Supabase migration bohun_uk_hold_staging_schema (20261021211458).
-- Reference copy only; do not reapply. Live columns, constraints and RLS matched this
-- file in the read-only preflight of 2026-10-22 (uk_R7_READONLY_PREFLIGHT.sql).
-- All data stays metadata-only and QA HOLD; publishing is structurally impossible here.

BEGIN;

CREATE SCHEMA bohun_uk_intake;
REVOKE ALL ON SCHEMA bohun_uk_intake FROM PUBLIC, anon, authenticated, service_role;

CREATE TABLE bohun_uk_intake.notices (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    external_id text NOT NULL UNIQUE CHECK (length(external_id) BETWEEN 1 AND 512),
    ocid text NOT NULL CHECK (length(ocid) BETWEEN 1 AND 512),
    release_id text NOT NULL CHECK (length(release_id) BETWEEN 1 AND 512),
    title text NOT NULL CHECK (length(btrim(title)) > 0),
    published_at timestamptz,
    notice_url text,                            -- NULL unless actually verified from source
    source_record_sha256 text NOT NULL
        CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    tags text[] NOT NULL DEFAULT ARRAY[]::text[],
    metadata jsonb NOT NULL,                     -- restricted BOHUN + UK FTS provenance only
    kind text NOT NULL DEFAULT 'tender' CHECK (kind = 'tender'),
    qa_status text NOT NULL DEFAULT 'HOLD' CHECK (qa_status = 'HOLD'),
    verification_status text NOT NULL DEFAULT 'UNVERIFIED'
        CHECK (verification_status = 'UNVERIFIED'),
    public_release boolean NOT NULL DEFAULT false CHECK (public_release = false),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    last_changed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_release_identity UNIQUE (ocid, release_id),
    CONSTRAINT uk_hold_payload CHECK (
        jsonb_typeof(metadata) = 'object'
        AND coalesce(metadata #>> '{bohun,qa}', '') = 'HOLD'
        AND coalesce(metadata #>> '{bohun,verification_status}', '') = 'UNVERIFIED'
        AND (metadata #> '{bohun,public_release}') IS NOT DISTINCT FROM 'false'::jsonb
        AND (metadata #> '{bohun,ready_for_import}') IS NOT DISTINCT FROM 'false'::jsonb
        AND coalesce(metadata #>> '{uk_fts,ocid}', '') = ocid
        AND coalesce(metadata #>> '{uk_fts,release_id}', '') = release_id
        AND coalesce(metadata #>> '{uk_fts,source_record_sha256}', '') = source_record_sha256
    ),
    CONSTRAINT uk_observation_order CHECK (last_seen_at >= first_seen_at)
);

CREATE INDEX uk_notices_published_at_idx
    ON bohun_uk_intake.notices (published_at DESC NULLS LAST);

CREATE TABLE bohun_uk_intake.notice_versions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    notice_id bigint NOT NULL REFERENCES bohun_uk_intake.notices(id),
    old_record_sha256 text NOT NULL CHECK (old_record_sha256 ~ '^[0-9a-f]{64}$'),
    new_record_sha256 text NOT NULL CHECK (new_record_sha256 ~ '^[0-9a-f]{64}$'),
    observed_at timestamptz NOT NULL DEFAULT now(),
    reason text NOT NULL DEFAULT 'SOURCE_METADATA_CHANGED',
    CONSTRAINT uk_version_changed CHECK (old_record_sha256 <> new_record_sha256),
    CONSTRAINT uk_version_unique UNIQUE (notice_id, new_record_sha256)
);

CREATE TABLE bohun_uk_intake.run_receipts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    status text NOT NULL DEFAULT 'STARTED'
        CHECK (status IN ('STARTED','COMPLETE','FAILED')),
    source_window_complete boolean NOT NULL DEFAULT false,
    observed_releases integer NOT NULL DEFAULT 0 CHECK (observed_releases >= 0),
    tender_candidates integer NOT NULL DEFAULT 0 CHECK (tender_candidates >= 0),
    inserted_count integer NOT NULL DEFAULT 0 CHECK (inserted_count >= 0),
    updated_count integer NOT NULL DEFAULT 0 CHECK (updated_count >= 0),
    repeated_count integer NOT NULL DEFAULT 0 CHECK (repeated_count >= 0),
    error_code text,
    CONSTRAINT uk_window_order CHECK (window_start < window_end),
    CONSTRAINT uk_complete_requires_proof CHECK (
        status <> 'COMPLETE' OR (source_window_complete AND completed_at IS NOT NULL)
    )
);

CREATE TABLE bohun_uk_intake.checkpoints (
    source_code text PRIMARY KEY CHECK (source_code = 'uk_find_a_tender'),
    last_complete_window_end timestamptz,
    last_receipt_id uuid REFERENCES bohun_uk_intake.run_receipts(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Defense in depth. This schema is NOT to be added to PostgREST exposed schemas.
ALTER TABLE bohun_uk_intake.notices ENABLE ROW LEVEL SECURITY;
ALTER TABLE bohun_uk_intake.notice_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE bohun_uk_intake.run_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE bohun_uk_intake.checkpoints ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA bohun_uk_intake
    FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA bohun_uk_intake
    FROM PUBLIC, anon, authenticated, service_role;

COMMENT ON SCHEMA bohun_uk_intake IS
    'EXP-009 UK Find a Tender isolated QA HOLD staging; not public/API exposed.';
COMMENT ON TABLE bohun_uk_intake.notices IS
    'Sanitized source metadata only; never directly serves the site.';

COMMIT;
