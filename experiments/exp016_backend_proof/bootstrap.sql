-- SYNTHETIC DISPOSABLE BASELINE ONLY. Never a production migration.
-- Reconstructed from explicitly admitted schema-only read 2026-11-02, PostgreSQL17.11.
-- No business records, source texts, keys or raw metadata are included.
DO $$ BEGIN
 IF NOT EXISTS(SELECT FROM pg_catalog.pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_catalog.pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_catalog.pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN; END IF;
 IF NOT EXISTS(SELECT FROM pg_catalog.pg_roles WHERE rolname='exp016_other') THEN CREATE ROLE exp016_other NOLOGIN; END IF;
END $$;
CREATE TABLE companies.profile_source (
 id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
 company_id text NOT NULL,
 source_ref text NOT NULL,
 title text NOT NULL,
 source_url text NOT NULL,
 source_kind text NOT NULL,
 as_of date NOT NULL,
 original_bytes_archived boolean DEFAULT false NOT NULL,
 snapshot_sha256 text,
 captured_at timestamp with time zone,
 created_at timestamp with time zone DEFAULT now() NOT NULL,
 CONSTRAINT profile_source_check CHECK (((NOT original_bytes_archived) OR ((snapshot_sha256 ~ '^[0-9a-f]{64}$'::text) AND (captured_at IS NOT NULL)))),
 CONSTRAINT profile_source_company_id_fkey FOREIGN KEY (company_id) REFERENCES companies.company(id),
 CONSTRAINT profile_source_company_id_id_key UNIQUE (company_id, id),
 CONSTRAINT profile_source_company_id_source_ref_source_url_key UNIQUE (company_id, source_ref, source_url),
 CONSTRAINT profile_source_pkey PRIMARY KEY (id),
 CONSTRAINT profile_source_source_ref_check CHECK (((length(source_ref) >= 1) AND (length(source_ref) <= 100))),
 CONSTRAINT profile_source_source_url_check CHECK ((source_url ~ '^https://[^[:space:]]+$'::text))
);
ALTER TABLE companies.profile_source ENABLE ROW LEVEL SECURITY;

CREATE TABLE companies.profile_claim (
 id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
 company_id text NOT NULL,
 claim_ref text NOT NULL,
 field_path text NOT NULL,
 claim_value jsonb NOT NULL,
 assessment text NOT NULL,
 review_note text DEFAULT ''::text NOT NULL,
 content_sha256 text NOT NULL,
 qa_status text DEFAULT 'HOLD'::text NOT NULL,
 public_release boolean DEFAULT false NOT NULL,
 created_at timestamp with time zone DEFAULT now() NOT NULL,
 CONSTRAINT profile_claim_assessment_check CHECK ((assessment = ANY (ARRAY['registry_supported'::text, 'cross_source'::text, 'company_claim'::text, 'inferred_needs_review'::text]))),
 CONSTRAINT profile_claim_claim_ref_check CHECK (((length(claim_ref) >= 1) AND (length(claim_ref) <= 100))),
 CONSTRAINT profile_claim_company_id_claim_ref_content_sha256_key UNIQUE (company_id, claim_ref, content_sha256),
 CONSTRAINT profile_claim_company_id_fkey FOREIGN KEY (company_id) REFERENCES companies.company(id),
 CONSTRAINT profile_claim_company_id_id_key UNIQUE (company_id, id),
 CONSTRAINT profile_claim_content_sha256_check CHECK ((content_sha256 ~ '^[0-9a-f]{64}$'::text)),
 CONSTRAINT profile_claim_field_path_check CHECK ((("left"(field_path, 1) = '/'::text) AND (length(field_path) < 256))),
 CONSTRAINT profile_claim_pkey PRIMARY KEY (id),
 CONSTRAINT profile_claim_public_release_check CHECK ((public_release = false)),
 CONSTRAINT profile_claim_qa_status_check CHECK ((qa_status = ANY (ARRAY['HOLD'::text, 'APPROVED'::text, 'REJECTED'::text])))
);
ALTER TABLE companies.profile_claim ENABLE ROW LEVEL SECURITY;

CREATE INDEX profile_claim_company_path_idx ON companies.profile_claim USING btree (company_id, field_path);

CREATE TABLE companies.profile_claim_source (
 company_id text NOT NULL,
 claim_id bigint NOT NULL,
 source_id bigint NOT NULL,
 CONSTRAINT profile_claim_source_company_id_claim_id_fkey FOREIGN KEY (company_id, claim_id) REFERENCES companies.profile_claim(company_id, id),
 CONSTRAINT profile_claim_source_company_id_fkey FOREIGN KEY (company_id) REFERENCES companies.company(id),
 CONSTRAINT profile_claim_source_company_id_source_id_fkey FOREIGN KEY (company_id, source_id) REFERENCES companies.profile_source(company_id, id),
 CONSTRAINT profile_claim_source_pkey PRIMARY KEY (claim_id, source_id)
);
ALTER TABLE companies.profile_claim_source ENABLE ROW LEVEL SECURITY;

CREATE TABLE companies.g4_section_review (
 company_id text NOT NULL,
 section_key text NOT NULL,
 coverage_state text DEFAULT 'NOT_RESEARCHED'::text NOT NULL,
 reviewed_at timestamp with time zone,
 review_note text DEFAULT ''::text NOT NULL,
 qa_status text DEFAULT 'HOLD'::text NOT NULL,
 public_release boolean DEFAULT false NOT NULL,
 updated_at timestamp with time zone DEFAULT now() NOT NULL,
 CONSTRAINT g4_section_review_check CHECK (((coverage_state = 'NOT_RESEARCHED'::text) OR (reviewed_at IS NOT NULL))),
 CONSTRAINT g4_section_review_company_id_fkey FOREIGN KEY (company_id) REFERENCES companies.company(id),
 CONSTRAINT g4_section_review_coverage_state_check CHECK ((coverage_state = ANY (ARRAY['NOT_RESEARCHED'::text, 'RESEARCHED_NOT_FOUND'::text, 'NOT_APPLICABLE'::text, 'HAS_HOLD_CLAIMS'::text, 'HAS_APPROVED_CLAIMS'::text]))),
 CONSTRAINT g4_section_review_pkey PRIMARY KEY (company_id, section_key),
 CONSTRAINT g4_section_review_public_release_check CHECK ((public_release = false)),
 CONSTRAINT g4_section_review_qa_status_check CHECK ((qa_status = ANY (ARRAY['HOLD'::text, 'APPROVED'::text, 'REJECTED'::text]))),
 CONSTRAINT g4_section_review_section_key_check CHECK ((section_key = ANY (ARRAY['IDENTITY'::text, 'CLASSIFICATION'::text, 'PRODUCTS'::text, 'TECHNOLOGIES'::text, 'PROGRAMMES'::text, 'EVENTS'::text, 'RELATIONSHIPS'::text, 'FINANCIALS'::text, 'GEOGRAPHY'::text, 'SOURCES'::text])))
);
ALTER TABLE companies.g4_section_review ENABLE ROW LEVEL SECURITY;

-- Fixture-only trusted input. Not the A26 reader or source admission implementation.
CREATE SCHEMA exp016_fixture;
REVOKE ALL ON SCHEMA exp016_fixture FROM PUBLIC;
CREATE TABLE exp016_fixture.profile (
 company_id text PRIMARY KEY REFERENCES companies.company(id),
 card jsonb NOT NULL,context jsonb NOT NULL,properties_json text NOT NULL,
 research jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE exp016_fixture.source (
 company_id text NOT NULL,source_ref text NOT NULL,url text NOT NULL,text_hash text NOT NULL,
 binding text NOT NULL,admitted boolean NOT NULL DEFAULT true,verified jsonb NOT NULL DEFAULT '[]',
 PRIMARY KEY(company_id,source_ref,url,text_hash)
);
CREATE FUNCTION exp016_fixture.profile_reader(ids text[]) RETURNS jsonb LANGUAGE plpgsql
SET search_path='' AS $$
DECLARE result jsonb;
BEGIN
 IF (SELECT count(*) FROM exp016_fixture.profile WHERE company_id=ANY(ids))<>pg_catalog.cardinality(ids)
 THEN RAISE EXCEPTION USING ERRCODE='P1600',DETAIL='PARTIAL_CARD_READ',MESSAGE='FIXTURE_REJECTED'; END IF;
 SELECT pg_catalog.jsonb_build_object(
 'source_revision',companies._automation_hash((SELECT pg_catalog.jsonb_agg(pg_catalog.to_jsonb(p) ORDER BY company_id)
   FROM exp016_fixture.profile p WHERE company_id=ANY(ids))),
 'cards',(SELECT pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('company',card,'properties_json',properties_json,
   'read_at',companies._automation_time(clock_timestamp()),'complete',true,'manual_ownership_complete',true) ORDER BY company_id)
   FROM exp016_fixture.profile WHERE company_id=ANY(ids)),
 'contexts',(SELECT pg_catalog.jsonb_object_agg(company_id,context) FROM exp016_fixture.profile WHERE company_id=ANY(ids)),
 'section_states',(SELECT coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('company_id',company_id,'section',v->>0,
   'value',pg_catalog.jsonb_build_object('_type','SectionState','coverage',v->>1,'next_due',null,'input_hash',null)) ORDER BY company_id,v->>0),'[]')
   FROM exp016_fixture.profile CROSS JOIN LATERAL pg_catalog.jsonb_array_elements(context#>'{view,section_states}') e(v)
   WHERE company_id=ANY(ids)),
 'research_admitted',(SELECT pg_catalog.jsonb_object_agg(company_id,research) FROM exp016_fixture.profile WHERE company_id=ANY(ids))) INTO result;
 RETURN result;
END $$;
CREATE FUNCTION exp016_fixture.source_reader(cid text,ref text,url_value text,hash_value text)
RETURNS jsonb LANGUAGE sql SET search_path='' AS $$
 SELECT pg_catalog.jsonb_build_object('admitted',admitted,'text_hash',text_hash,'binding',binding,'verified_content_hashes',verified)
 FROM exp016_fixture.source WHERE company_id=cid AND source_ref=ref AND url=url_value AND text_hash=hash_value
$$;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA exp016_fixture FROM PUBLIC;
