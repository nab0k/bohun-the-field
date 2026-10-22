-- EXP-009 R7: EXACT SQL APPLIED to Supabase bohundefence-intelligence
-- Migration registered as bohun_uk_stage_writer_nologin_grants (20261021225922). Do not reapply.
-- Grant to NOLOGIN role only. No user password/secret is included.
BEGIN;
CREATE ROLE bohun_uk_ingest_writer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA bohun_uk_intake TO bohun_uk_ingest_writer;
GRANT SELECT ON bohun_uk_intake.notices TO bohun_uk_ingest_writer;
GRANT INSERT (external_id,ocid,release_id,title,published_at,notice_url,source_record_sha256,tags,metadata)
    ON bohun_uk_intake.notices TO bohun_uk_ingest_writer;
GRANT UPDATE (title,published_at,source_record_sha256,tags,metadata,last_seen_at,last_changed_at)
    ON bohun_uk_intake.notices TO bohun_uk_ingest_writer;
GRANT SELECT ON bohun_uk_intake.notice_versions TO bohun_uk_ingest_writer;
GRANT INSERT (notice_id,old_record_sha256,new_record_sha256)
    ON bohun_uk_intake.notice_versions TO bohun_uk_ingest_writer;
GRANT SELECT ON bohun_uk_intake.run_receipts TO bohun_uk_ingest_writer;
GRANT INSERT (window_start,window_end,started_at,completed_at,status,source_window_complete,
              observed_releases,tender_candidates,inserted_count,updated_count,repeated_count)
    ON bohun_uk_intake.run_receipts TO bohun_uk_ingest_writer;
-- Deliberately NO access to checkpoints or any intelligence.*, exp009.* or public objects.
CREATE POLICY uk_ingest_read_hold ON bohun_uk_intake.notices
    FOR SELECT TO bohun_uk_ingest_writer USING (qa_status='HOLD' AND public_release=false);
CREATE POLICY uk_ingest_insert_hold ON bohun_uk_intake.notices
    FOR INSERT TO bohun_uk_ingest_writer
    WITH CHECK (qa_status='HOLD' AND public_release=false AND verification_status='UNVERIFIED');
CREATE POLICY uk_ingest_update_hold ON bohun_uk_intake.notices
    FOR UPDATE TO bohun_uk_ingest_writer USING (qa_status='HOLD' AND public_release=false)
    WITH CHECK (qa_status='HOLD' AND public_release=false AND verification_status='UNVERIFIED');
CREATE POLICY uk_ingest_read_versions ON bohun_uk_intake.notice_versions
    FOR SELECT TO bohun_uk_ingest_writer USING (true);
CREATE POLICY uk_ingest_insert_versions ON bohun_uk_intake.notice_versions
    FOR INSERT TO bohun_uk_ingest_writer WITH CHECK (true);
CREATE POLICY uk_ingest_read_receipts ON bohun_uk_intake.run_receipts
    FOR SELECT TO bohun_uk_ingest_writer USING (true);
CREATE POLICY uk_ingest_insert_receipts ON bohun_uk_intake.run_receipts
    FOR INSERT TO bohun_uk_ingest_writer WITH CHECK (status='COMPLETE' AND source_window_complete=true);
COMMIT;
