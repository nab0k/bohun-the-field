-- knowledge_item_processing_v14 (EXP-024 DESIGN v1 §5): which input item was processed by which route version.
-- Additive; existing objects and their privileges are not changed.
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '20s';
DO $pre$ BEGIN
  IF current_user <> 'postgres' OR to_regnamespace('knowledge') IS NULL OR to_regclass('knowledge.item_processing') IS NOT NULL
  THEN RAISE EXCEPTION 'ITEM_PROCESSING_V14_PREFLIGHT'; END IF;
END $pre$;
CREATE TABLE knowledge.item_processing (
  item_id          bigint NOT NULL REFERENCES intelligence.items(id),
  pipeline_version text   NOT NULL,
  outcome          text   NOT NULL CHECK (outcome IN ('event','reject','hold','error')),
  reason_codes     text[] NOT NULL DEFAULT '{}',
  event_id         bigint REFERENCES knowledge.event(id),
  cost_micro_usd   bigint NOT NULL DEFAULT 0,
  processed_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (item_id, pipeline_version)
);
ALTER TABLE knowledge.item_processing ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON knowledge.item_processing FROM PUBLIC, anon, authenticated;
DO $verify$ BEGIN
  IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = 'knowledge.item_processing'::regclass)
  OR (SELECT relowner FROM pg_class WHERE oid = 'knowledge.item_processing'::regclass) <> 'postgres'::regrole
  OR has_table_privilege('anon', 'knowledge.item_processing', 'SELECT')
  OR has_table_privilege('authenticated', 'knowledge.item_processing', 'SELECT')
  THEN RAISE EXCEPTION 'ITEM_PROCESSING_V14_VERIFY_FAILED'; END IF;
END $verify$;
