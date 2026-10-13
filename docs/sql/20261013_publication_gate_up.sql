-- REVIEW ONLY: no production application, no seed and no implicit APPROVE.
-- Additive publication ledger. The source UUID format must be confirmed before ingest;
-- storing an external ID as TEXT avoids falsely asserting the item PK type.
BEGIN;
CREATE SCHEMA IF NOT EXISTS publication;
CREATE TABLE publication.release_decisions (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 source_item_id text NOT NULL CHECK (length(source_item_id) BETWEEN 1 AND 160),
 source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
 source_url text NOT NULL CHECK (source_url ~ '^https://[^[:space:]]+$'),
 headline text NOT NULL CHECK (length(trim(headline)) BETWEEN 1 AND 300),
 published_at timestamptz NOT NULL,
 country_iso2 text CHECK (country_iso2 ~ '^[A-Z]{2}$'),
 event_id text,
 takeaway text CHECK (length(takeaway) <= 1200),
 qa_decision text NOT NULL DEFAULT 'HOLD' CHECK (qa_decision IN ('HOLD','REJECT','APPROVE')),
 public_release boolean NOT NULL DEFAULT false,
 rights_checked boolean NOT NULL DEFAULT false,
 evidence_checked boolean NOT NULL DEFAULT false,
 reviewed_by text,
 reviewed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT publish_gate CHECK (
  NOT public_release OR (
   qa_decision = 'APPROVE'
   AND rights_checked AND evidence_checked
   AND length(trim(coalesce(reviewed_by,''))) > 0
   AND reviewed_at IS NOT NULL
  )
 ),
 CONSTRAINT no_future_review CHECK (reviewed_at IS NULL OR reviewed_at <= now())
);
CREATE UNIQUE INDEX release_decisions_item_hash_uidx
 ON publication.release_decisions(source_item_id,source_sha256);
CREATE TABLE publication.decision_audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 decision_id bigint NOT NULL REFERENCES publication.release_decisions(id) ON DELETE RESTRICT,
 action text NOT NULL,
 actor text NOT NULL,
 previous_state jsonb,
 resulting_state jsonb NOT NULL,
 recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION publication.track_release_decision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP = 'DELETE' THEN
  RAISE EXCEPTION 'release decisions are not deletable';
 END IF;
 IF TG_OP = 'UPDATE' THEN
  IF NEW.source_item_id IS DISTINCT FROM OLD.source_item_id
    OR NEW.source_sha256 IS DISTINCT FROM OLD.source_sha256
    OR NEW.source_url IS DISTINCT FROM OLD.source_url
    OR NEW.headline IS DISTINCT FROM OLD.headline
    OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
    RAISE EXCEPTION 'immutable provenance changed: create another decision';
  END IF;
 END IF;
 INSERT INTO publication.decision_audit(decision_id, action, actor, previous_state, resulting_state)
 VALUES(NEW.id,TG_OP,current_user,CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END,to_jsonb(NEW));
 RETURN NEW;
END $$;
CREATE TRIGGER release_decision_audit_trigger
 AFTER INSERT OR UPDATE ON publication.release_decisions
 FOR EACH ROW EXECUTE FUNCTION publication.track_release_decision();
CREATE FUNCTION publication.block_audit_modifications() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'decision audit is append-only'; END $$;
CREATE TRIGGER immutable_decision_audit
 BEFORE UPDATE OR DELETE ON publication.decision_audit
 FOR EACH ROW EXECUTE FUNCTION publication.block_audit_modifications();
-- View is intentionally empty until a real reviewer-approved record exists.
-- Only explicit fields are projected. No raw text, private addresses or model drafts.
CREATE VIEW publication.public_approved_intelligence
 WITH (security_barrier=true) AS
 SELECT id::text AS event_id, headline, published_at, country_iso2,
        source_url, source_sha256 AS content_hash, takeaway,
        jsonb_build_object('decision',qa_decision,'public_release',public_release) AS qa
 FROM publication.release_decisions
 WHERE qa_decision='APPROVE' AND public_release
   AND rights_checked AND evidence_checked
   AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL;
ALTER TABLE publication.release_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE publication.decision_audit ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON SCHEMA publication FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA publication FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA publication FROM PUBLIC;
-- The view owner must own release_decisions to read the approved projection.
-- Do not use FORCE RLS here: with zero policies it would make the view always empty.
-- Restrict direct access through REVOKE and a separately granted SELECT on the view.
-- No API role created/granted and no exposed schema setting changed here.
-- A reviewed dedicated read-only credential plus PostgREST schema exposure is required separately.
COMMIT;
