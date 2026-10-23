-- ROLLBACK, DESTRUCTIVE to newly added publication decision history. Export first.
BEGIN;
DROP VIEW IF EXISTS publication.public_approved_intelligence;
DROP TABLE IF EXISTS publication.decision_audit;
DROP TRIGGER IF EXISTS release_decision_audit_trigger ON publication.release_decisions;
DROP TABLE IF EXISTS publication.release_decisions;
DROP FUNCTION IF EXISTS publication.track_release_decision();
DROP FUNCTION IF EXISTS publication.block_audit_modifications();
COMMIT;
