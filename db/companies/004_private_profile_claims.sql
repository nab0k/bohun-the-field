-- BOHUN G2: PRIVATE PROFILE STAGING / REVIEW ONLY; never auto-applied.
-- Requires exact owner approval before any live migration or profile writes.
CREATE TABLE companies.profile_source (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  company_id text NOT NULL REFERENCES companies.company(id),
  source_ref text NOT NULL CHECK (length(source_ref) BETWEEN 1 AND 100),
  title text NOT NULL,
  source_url text NOT NULL CHECK (source_url ~ '^https://[^[:space:]]+$'),
  source_kind text NOT NULL,
  as_of date NOT NULL,
  original_bytes_archived boolean NOT NULL DEFAULT false,
  snapshot_sha256 text,
  captured_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (company_id,source_ref,source_url), UNIQUE(company_id,id),
  CHECK (NOT original_bytes_archived OR
    (snapshot_sha256 ~ '^[0-9a-f]{64}$' AND captured_at IS NOT NULL))
);
CREATE TABLE companies.profile_claim (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  company_id text NOT NULL REFERENCES companies.company(id),
  claim_ref text NOT NULL CHECK (length(claim_ref) BETWEEN 1 AND 100),
  field_path text NOT NULL CHECK (left(field_path,1)='/' AND length(field_path)<256),
  claim_value jsonb NOT NULL,
  assessment text NOT NULL CHECK (assessment IN (
     'registry_supported','cross_source','company_claim','inferred_needs_review')),
  review_note text NOT NULL DEFAULT '',
  content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  qa_status text NOT NULL DEFAULT 'HOLD' CHECK (qa_status IN ('HOLD','APPROVED','REJECTED')),
  public_release boolean NOT NULL DEFAULT false CHECK (public_release=false),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(company_id,claim_ref,content_sha256), UNIQUE(company_id,id)
);
CREATE TABLE companies.profile_claim_source (
  company_id text NOT NULL REFERENCES companies.company(id),
  claim_id bigint NOT NULL, source_id bigint NOT NULL,
  PRIMARY KEY(claim_id,source_id),
  FOREIGN KEY(company_id,claim_id) REFERENCES companies.profile_claim(company_id,id),
  FOREIGN KEY(company_id,source_id) REFERENCES companies.profile_source(company_id,id)
);
CREATE TABLE companies.profile_research_case (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  candidate_id bigint NOT NULL REFERENCES companies.discovery_candidate(id),
  research_payload jsonb NOT NULL,
  content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
  qa_status text NOT NULL DEFAULT 'HOLD' CHECK (qa_status='HOLD'),
  public_release boolean NOT NULL DEFAULT false CHECK (public_release=false),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(candidate_id,content_sha256)
);
CREATE INDEX profile_claim_company_path_idx ON companies.profile_claim(company_id,field_path);
CREATE INDEX profile_case_candidate_idx ON companies.profile_research_case(candidate_id);
ALTER TABLE companies.profile_source ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.profile_claim ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.profile_claim_source ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.profile_research_case ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON companies.profile_source,companies.profile_claim,
  companies.profile_claim_source,companies.profile_research_case FROM PUBLIC;
REVOKE ALL ON companies.profile_source,companies.profile_claim,
  companies.profile_claim_source,companies.profile_research_case
  FROM anon,authenticated,service_role;
REVOKE ALL ON SEQUENCE companies.profile_source_id_seq,
  companies.profile_claim_id_seq,companies.profile_research_case_id_seq
  FROM PUBLIC,anon,authenticated,service_role;
-- No API grants/policies, public view, jobs, publication, or production writes.
