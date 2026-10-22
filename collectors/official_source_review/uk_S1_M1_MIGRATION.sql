-- EXP-009 stage 1, migration M1: one notice per ocid + append-only release versions.
-- APPLIED by the owner in the Supabase SQL Editor on 2026-10-22 after D8 (reference copy;
-- do not reapply). Not registered in supabase_migrations because it ran from the SQL Editor.
-- Post-migration read-only preflight (uk_S1_READONLY_PREFLIGHT.sql) passed.
-- HOLD / UNVERIFIED / public_release=false, RLS and API-role isolation are unchanged.
BEGIN;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM bohun_uk_intake.notices)
     OR EXISTS (SELECT 1 FROM bohun_uk_intake.notice_versions) THEN
    RAISE EXCEPTION 'M1 requires empty notices and notice_versions (run D8 first)';
  END IF;
END $$;

-- 1. Versions: the empty R7 change table is replaced by one row per source release.
DROP TABLE bohun_uk_intake.notice_versions;

-- 2. notices: identity moves from (ocid, release_id) to ocid.
ALTER TABLE bohun_uk_intake.notices DROP CONSTRAINT uk_hold_payload;
ALTER TABLE bohun_uk_intake.notices DROP CONSTRAINT uk_release_identity;
ALTER TABLE bohun_uk_intake.notices DROP COLUMN tags;
ALTER TABLE bohun_uk_intake.notices RENAME COLUMN release_id TO latest_release_id;
ALTER TABLE bohun_uk_intake.notices RENAME COLUMN source_record_sha256 TO latest_source_sha256;
ALTER TABLE bohun_uk_intake.notices RENAME COLUMN published_at TO latest_release_date;
ALTER TABLE bohun_uk_intake.notices ALTER COLUMN latest_release_date SET NOT NULL;
ALTER INDEX IF EXISTS bohun_uk_intake.uk_notices_published_at_idx RENAME TO uk_notices_latest_release_date_idx;

ALTER TABLE bohun_uk_intake.notices
    ADD COLUMN description text CHECK (length(description) <= 20000),
    ADD COLUMN tender_status text CHECK (length(tender_status) <= 2000),
    ADD COLUMN procurement_method text CHECK (length(procurement_method) <= 2000),
    ADD COLUMN main_category text CHECK (length(main_category) <= 2000),
    ADD COLUMN value_amount numeric CHECK (value_amount >= 0),
    ADD COLUMN value_amount_gross numeric CHECK (value_amount_gross >= 0),
    ADD COLUMN value_currency text CHECK (value_currency ~ '^[A-Z]{3}$'),
    ADD COLUMN tender_deadline timestamptz,
    ADD COLUMN tender_deadline_raw text CHECK (length(tender_deadline_raw) <= 40),
    ADD COLUMN buyer_id text CHECK (length(buyer_id) <= 256),
    ADD COLUMN buyer_name text CHECK (length(buyer_name) <= 512),
    ADD COLUMN buyer_country text CHECK (length(buyer_country) <= 128),
    ADD COLUMN cpv_codes text[] NOT NULL DEFAULT ARRAY[]::text[]
        CHECK (array_to_string(cpv_codes, ',') ~ '^([0-9]{8}(,[0-9]{8})*)?$'),
    ADD COLUMN lots_count integer CHECK (lots_count >= 0),
    ADD COLUMN above_threshold boolean,
    ADD COLUMN completeness text NOT NULL
        CHECK (completeness IN ('HAS_TENDER_NOTICE', 'UPDATE_ONLY'));

ALTER TABLE bohun_uk_intake.notices
    ADD CONSTRAINT notices_ocid_key UNIQUE (ocid),
    ADD CONSTRAINT uk_external_is_ocid CHECK (external_id = ocid),
    ADD CONSTRAINT uk_notice_url_from_fts CHECK (
        notice_url IS NULL OR notice_url LIKE 'https://www.find-tender.service.gov.uk/Notice/%'),
    ADD CONSTRAINT uk_hold_payload CHECK (
        jsonb_typeof(metadata) = 'object'
        AND coalesce(metadata #>> '{bohun,qa}', '') = 'HOLD'
        AND coalesce(metadata #>> '{bohun,verification_status}', '') = 'UNVERIFIED'
        AND (metadata #> '{bohun,public_release}') IS NOT DISTINCT FROM 'false'::jsonb
        AND (metadata #> '{bohun,ready_for_import}') IS NOT DISTINCT FROM 'false'::jsonb);

-- 3. One row per source release; contact points are structurally excluded (D3).
CREATE TABLE bohun_uk_intake.notice_versions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    notice_id bigint NOT NULL REFERENCES bohun_uk_intake.notices(id),
    ocid text NOT NULL CHECK (length(ocid) BETWEEN 1 AND 512),
    release_id text NOT NULL CHECK (length(release_id) BETWEEN 1 AND 512),
    release_tag text NOT NULL CHECK (release_tag IN ('tender', 'tenderUpdate')),
    release_date timestamptz NOT NULL,
    source_record_sha256 text NOT NULL CHECK (source_record_sha256 ~ '^[0-9a-f]{64}$'),
    release_json jsonb NOT NULL CHECK (
        jsonb_typeof(release_json) = 'object'
        AND octet_length(release_json::text) <= 65536
        AND NOT jsonb_path_exists(release_json, '$.parties[*].contactPoint')
        AND release_json ->> 'ocid' = ocid
        AND release_json ->> 'id' = release_id),
    package_license text NOT NULL CHECK (package_license LIKE 'http%' AND length(package_license) <= 300),
    package_publisher text NOT NULL CHECK (length(package_publisher) BETWEEN 1 AND 200),
    applied_to_current boolean NOT NULL,
    reason text NOT NULL CHECK (reason IN ('NEW_RELEASE', 'SOURCE_REPUBLISHED')),
    observed_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uk_version_unique UNIQUE (ocid, release_id, source_record_sha256)
);
CREATE INDEX uk_versions_notice_idx ON bohun_uk_intake.notice_versions (notice_id, release_date);
ALTER TABLE bohun_uk_intake.notice_versions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON bohun_uk_intake.notice_versions FROM PUBLIC, anon, authenticated, service_role;

-- 4. Receipts: release counters.
ALTER TABLE bohun_uk_intake.run_receipts
    ADD COLUMN releases_seen_tender integer NOT NULL DEFAULT 0 CHECK (releases_seen_tender >= 0),
    ADD COLUMN releases_seen_update integer NOT NULL DEFAULT 0 CHECK (releases_seen_update >= 0),
    ADD COLUMN versions_inserted integer NOT NULL DEFAULT 0 CHECK (versions_inserted >= 0);

-- 5. Writer grants (column level, as before). No DELETE anywhere; versions append-only.
REVOKE INSERT, UPDATE ON bohun_uk_intake.notices FROM bohun_uk_ingest_writer;
GRANT INSERT (external_id, ocid, latest_release_id, latest_release_date, latest_source_sha256,
              completeness, metadata, title, description, tender_status, procurement_method,
              main_category, value_amount, value_amount_gross, value_currency, tender_deadline,
              tender_deadline_raw, buyer_id, buyer_name, buyer_country, cpv_codes, lots_count,
              above_threshold, notice_url)
    ON bohun_uk_intake.notices TO bohun_uk_ingest_writer;
GRANT UPDATE (latest_release_id, latest_release_date, latest_source_sha256, completeness,
              title, description, tender_status, procurement_method, main_category,
              value_amount, value_amount_gross, value_currency, tender_deadline,
              tender_deadline_raw, buyer_id, buyer_name, buyer_country, cpv_codes, lots_count,
              above_threshold, notice_url, last_seen_at, last_changed_at)
    ON bohun_uk_intake.notices TO bohun_uk_ingest_writer;

GRANT SELECT ON bohun_uk_intake.notice_versions TO bohun_uk_ingest_writer;
GRANT INSERT (notice_id, ocid, release_id, release_tag, release_date, source_record_sha256,
              release_json, package_license, package_publisher, applied_to_current, reason)
    ON bohun_uk_intake.notice_versions TO bohun_uk_ingest_writer;
CREATE POLICY uk_ingest_read_versions ON bohun_uk_intake.notice_versions
    FOR SELECT TO bohun_uk_ingest_writer USING (true);
CREATE POLICY uk_ingest_insert_versions ON bohun_uk_intake.notice_versions
    FOR INSERT TO bohun_uk_ingest_writer WITH CHECK (true);

GRANT INSERT (releases_seen_tender, releases_seen_update, versions_inserted)
    ON bohun_uk_intake.run_receipts TO bohun_uk_ingest_writer;

COMMENT ON TABLE bohun_uk_intake.notice_versions IS
    'EXP-009 stage 1: one sanitized UK FTS release per row (no contactPoint); HOLD staging only.';

COMMIT;
