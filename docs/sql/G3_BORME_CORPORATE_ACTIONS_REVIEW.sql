-- BOHUN G3 / executable PostgreSQL DDL; REVIEW ONLY, NOT APPLIED.
-- Gate: verify exposed schemas, SECURITY DEFINER RPCs, role grants and current DB state.
-- Deployment must use the repository's migration naming mechanism, not execute this file blindly.
BEGIN;
DO $$
BEGIN
  IF to_regclass('intelligence.items') IS NULL THEN
    RAISE EXCEPTION 'Missing intelligence.items';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint c
    WHERE c.conrelid = 'intelligence.items'::regclass AND c.contype = 'p'
      AND pg_get_constraintdef(c.oid) = 'PRIMARY KEY (id)'
  ) THEN
    RAISE EXCEPTION 'Unexpected intelligence.items primary key';
  END IF;
  IF to_regclass('intelligence.corporate_actions') IS NOT NULL THEN
    RAISE EXCEPTION 'intelligence.corporate_actions already exists: manual review required';
  END IF;
END $$;

CREATE TABLE intelligence.corporate_actions (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  item_id bigint NOT NULL REFERENCES intelligence.items(id) ON DELETE RESTRICT,
  action_ordinal integer NOT NULL CHECK (action_ordinal > 0),
  action_type text NOT NULL CHECK (length(btrim(action_type)) > 0),
  effective_date date,
  extraction_status text NOT NULL DEFAULT 'unreviewed'
    CHECK (extraction_status IN ('unreviewed','reviewed','rejected')),
  extraction_method text NOT NULL CHECK (length(btrim(extraction_method)) > 0),
  evidence_locator text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT corporate_actions_item_ordinal_unique UNIQUE (item_id, action_ordinal)
);

CREATE INDEX corporate_actions_item_id_idx
  ON intelligence.corporate_actions (item_id);

ALTER TABLE intelligence.corporate_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE intelligence.corporate_actions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE intelligence.corporate_actions FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON SEQUENCE intelligence.corporate_actions_id_seq FROM PUBLIC, anon, authenticated, service_role;
-- No policies or writer grants by design: deny by default.
-- Writer role / policies require separate audited migration.
COMMIT;
