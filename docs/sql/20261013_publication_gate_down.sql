-- Destructive rollback: deletes ONLY publication.release_decisions and its data.
-- Preserve exported decision history before executing if any decisions were entered.
BEGIN;
DROP TABLE IF EXISTS publication.release_decisions;
-- Do not drop publication schema: it may contain unrelated future objects.
COMMIT;
