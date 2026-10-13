-- PROPOSED ONLY. Not executed. #55 publication gate.
-- Preconditions: reviewed by database owner; apply with privileged migration role.
BEGIN;
CREATE SCHEMA IF NOT EXISTS publication;
CREATE TABLE publication.release_decisions (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 source_item_id uuid NOT NULL REFERENCES intelligence.items(id) ON DELETE RESTRICT,
 source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
 source_url text NOT NULL CHECK (source_url ~ '^https://[^[:space:]]+$'),
 headline text NOT NULL CHECK (length(trim(headline)) BETWEEN 1 AND 300),
 published_at timestamptz NOT NULL,
 country_iso2 text CHECK (country_iso2 ~ '^[A-Z]{2}$'),
 event_id text,
 takeaway text CHECK (length(takeaway) <= 1200),
 qa_decision text NOT NULL CHECK (qa_decision IN ('HOLD','REJECT','APPROVE')),
 public_release boolean NOT NULL DEFAULT false,
 reviewed_by text,
 reviewed_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT release_requires_review CHECK (
   NOT public_release OR (qa_decision='APPROVE' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
 )
);
CREATE UNIQUE INDEX release_decisions_item_hash_uidx ON publication.release_decisions(source_item_id,source_sha256);
ALTER TABLE publication.release_decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE publication.release_decisions FORCE ROW LEVEL SECURITY;
REVOKE ALL ON SCHEMA publication FROM PUBLIC, anon, authenticated;
REVOKE ALL ON ALL TABLES IN SCHEMA publication FROM PUBLIC, anon, authenticated;
-- No SELECT granted to an API role yet. Create dedicated role and audited
-- approved projection only after Cloudflare deployment route is confirmed.
COMMIT;
