-- EXP-009 stage 1: rollback of M1 to the R7 shape. NOT EXECUTED; owner approval; postgres.
-- Only for empty tables (stage-1 rows would have to be removed first, separately approved).
BEGIN;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM bohun_uk_intake.notices)
     OR EXISTS (SELECT 1 FROM bohun_uk_intake.notice_versions) THEN
    RAISE EXCEPTION 'M1 down requires empty notices and notice_versions';
  END IF;
END $$;

DROP TABLE bohun_uk_intake.notice_versions;

ALTER TABLE bohun_uk_intake.run_receipts
    DROP COLUMN releases_seen_tender, DROP COLUMN releases_seen_update, DROP COLUMN versions_inserted;

ALTER TABLE bohun_uk_intake.notices
    DROP CONSTRAINT uk_hold_payload, DROP CONSTRAINT notices_ocid_key,
    DROP CONSTRAINT uk_external_is_ocid, DROP CONSTRAINT uk_notice_url_from_fts;
ALTER TABLE bohun_uk_intake.notices
    DROP COLUMN description, DROP COLUMN tender_status, DROP COLUMN procurement_method,
    DROP COLUMN main_category, DROP COLUMN value_amount, DROP COLUMN value_amount_gross,
    DROP COLUMN value_currency, DROP COLUMN tender_deadline, DROP COLUMN tender_deadline_raw,
    DROP COLUMN buyer_id, DROP COLUMN buyer_name, DROP COLUMN buyer_country,
    DROP COLUMN cpv_codes, DROP COLUMN lots_count, DROP COLUMN above_threshold,
    DROP COLUMN completeness;
ALTER INDEX IF EXISTS bohun_uk_intake.uk_notices_latest_release_date_idx RENAME TO uk_notices_published_at_idx;
ALTER TABLE bohun_uk_intake.notices ALTER COLUMN latest_release_date DROP NOT NULL;
ALTER TABLE bohun_uk_intake.notices RENAME COLUMN latest_release_date TO published_at;
ALTER TABLE bohun_uk_intake.notices RENAME COLUMN latest_source_sha256 TO source_record_sha256;
ALTER TABLE bohun_uk_intake.notices RENAME COLUMN latest_release_id TO release_id;
ALTER TABLE bohun_uk_intake.notices ADD COLUMN tags text[] NOT NULL DEFAULT ARRAY[]::text[];
ALTER TABLE bohun_uk_intake.notices
    ADD CONSTRAINT uk_release_identity UNIQUE (ocid, release_id),
    ADD CONSTRAINT uk_hold_payload CHECK (
        jsonb_typeof(metadata) = 'object'
        AND coalesce(metadata #>> '{bohun,qa}', '') = 'HOLD'
        AND coalesce(metadata #>> '{bohun,verification_status}', '') = 'UNVERIFIED'
        AND (metadata #> '{bohun,public_release}') IS NOT DISTINCT FROM 'false'::jsonb
        AND (metadata #> '{bohun,ready_for_import}') IS NOT DISTINCT FROM 'false'::jsonb
        AND coalesce(metadata #>> '{uk_fts,ocid}', '') = ocid
        AND coalesce(metadata #>> '{uk_fts,release_id}', '') = release_id
        AND coalesce(metadata #>> '{uk_fts,source_record_sha256}', '') = source_record_sha256);

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
ALTER TABLE bohun_uk_intake.notice_versions ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON bohun_uk_intake.notice_versions FROM PUBLIC, anon, authenticated, service_role;

REVOKE INSERT, UPDATE ON bohun_uk_intake.notices FROM bohun_uk_ingest_writer;
GRANT INSERT (external_id,ocid,release_id,title,published_at,notice_url,source_record_sha256,tags,metadata)
    ON bohun_uk_intake.notices TO bohun_uk_ingest_writer;
GRANT UPDATE (title,published_at,source_record_sha256,tags,metadata,last_seen_at,last_changed_at)
    ON bohun_uk_intake.notices TO bohun_uk_ingest_writer;
GRANT SELECT ON bohun_uk_intake.notice_versions TO bohun_uk_ingest_writer;
GRANT INSERT (notice_id,old_record_sha256,new_record_sha256)
    ON bohun_uk_intake.notice_versions TO bohun_uk_ingest_writer;
CREATE POLICY uk_ingest_read_versions ON bohun_uk_intake.notice_versions
    FOR SELECT TO bohun_uk_ingest_writer USING (true);
CREATE POLICY uk_ingest_insert_versions ON bohun_uk_intake.notice_versions
    FOR INSERT TO bohun_uk_ingest_writer WITH CHECK (true);

COMMIT;
