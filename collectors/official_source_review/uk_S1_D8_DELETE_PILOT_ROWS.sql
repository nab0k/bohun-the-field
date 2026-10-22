-- EXP-009 stage 1, decision D8: remove the 10 R7.1 pilot notices before migration M1.
-- EXECUTED by the owner in the Supabase SQL Editor on 2026-10-22 (reference copy; do not rerun).
-- Run as postgres (the writer role has no DELETE). Pilot receipts in run_receipts were kept.
BEGIN;

DO $$
BEGIN
  IF (SELECT count(*) FROM bohun_uk_intake.notices) <> 10
     OR (SELECT count(*) FROM bohun_uk_intake.notice_versions) <> 0
     OR (SELECT count(*) FROM bohun_uk_intake.notices
         WHERE external_id <> ocid || '::' || release_id) <> 0 THEN
    RAISE EXCEPTION 'D8: unexpected state, expected exactly the 10 pilot notices and 0 versions';
  END IF;
END $$;

DELETE FROM bohun_uk_intake.notices;

COMMIT;
