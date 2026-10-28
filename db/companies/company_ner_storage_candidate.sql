-- OWNER_FIX01 candidate, NOT installed. Requires existing intelligence.items and exp009.
-- No grants to API roles, new roles, RPC, or changes to INPUT29 jobs/runtime_api.
BEGIN;
SET LOCAL lock_timeout='5s';
SET LOCAL statement_timeout='30s';
DO $preflight$ BEGIN
 IF current_user<>'postgres' OR NOT has_schema_privilege(current_user,'exp009','USAGE')
 OR (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='exp009')<>'exp009_owner'
 OR NOT has_column_privilege('exp009_owner','intelligence.items','id','REFERENCES')
 OR NOT EXISTS(SELECT 1 FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid
   JOIN pg_roles m ON m.oid=a.member JOIN pg_roles g ON g.oid=a.grantor
   WHERE r.rolname='exp009_owner' AND m.rolname=current_user AND g.rolname='supabase_admin'
     AND a.admin_option AND NOT a.inherit_option AND NOT a.set_option)
 OR EXISTS(SELECT 1 FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid
   WHERE r.rolname='exp009_owner' AND a.member=(SELECT oid FROM pg_roles WHERE rolname=current_user)
     AND a.grantor=a.member)
 THEN RAISE EXCEPTION 'NER_OWNER_FIX_PREFLIGHT'; END IF;
END $preflight$;
-- Temporary self-grant only; the original supabase_admin membership is unchanged.
GRANT exp009_owner TO CURRENT_USER WITH SET TRUE GRANTED BY CURRENT_USER;
GRANT exp009_owner TO CURRENT_USER WITH INHERIT FALSE GRANTED BY CURRENT_USER;
SET LOCAL ROLE exp009_owner;
CREATE TABLE exp009.company_ner_cache (
 job_key text PRIMARY KEY CHECK(job_key ~ '^[0-9a-f]{64}$'),
 contract_hash text NOT NULL CHECK(contract_hash ~ '^[0-9a-f]{64}$'),
 contract jsonb NOT NULL,
 text_sha256 text NOT NULL CHECK(text_sha256 ~ '^[0-9a-f]{64}$'),
 body text NOT NULL CHECK(octet_length(body) BETWEEN 200 AND 131072),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','running','complete','hold')),
 token uuid, started_at timestamptz, finished_at timestamptz,
 result jsonb, hold_reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(contract_hash,text_sha256),
 CHECK ((state='pending' AND token IS NULL AND result IS NULL)
     OR (state='running' AND token IS NOT NULL AND started_at IS NOT NULL AND result IS NULL)
     OR (state='complete' AND token IS NOT NULL AND result IS NOT NULL AND finished_at IS NOT NULL)
     OR (state='hold' AND result IS NULL AND hold_reason IS NOT NULL))
);
CREATE INDEX company_ner_pending ON exp009.company_ner_cache(contract_hash,created_at,job_key) WHERE state='pending';
CREATE TABLE exp009.company_ner_inputs (
 input_key text PRIMARY KEY CHECK(input_key ~ '^[0-9a-f]{64}$'),
 item_id bigint NOT NULL REFERENCES intelligence.items(id),
 source_updated_at timestamptz NOT NULL,
 contract_hash text NOT NULL,
 job_key text REFERENCES exp009.company_ner_cache(job_key),
 document jsonb NOT NULL,
 intake_reason text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(item_id,source_updated_at,contract_hash),
 CHECK ((job_key IS NOT NULL AND intake_reason IS NULL) OR
        (job_key IS NULL AND intake_reason IS NOT NULL))
);
CREATE INDEX company_ner_inputs_job ON exp009.company_ner_inputs(job_key,input_key);
CREATE TABLE exp009.company_ner_links (
 input_key text NOT NULL REFERENCES exp009.company_ner_inputs(input_key),
 catalog_hash text NOT NULL CHECK(catalog_hash ~ '^[0-9a-f]{64}$'),
 linker_version text NOT NULL,
 packet jsonb NOT NULL,
 public_release boolean NOT NULL DEFAULT false CHECK(public_release=false),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(input_key,catalog_hash,linker_version),
 CHECK(coalesce(packet->'public_release'='false'::jsonb AND packet->'confirmed_links'='[]'::jsonb,false))
);
ALTER TABLE exp009.company_ner_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE exp009.company_ner_inputs ENABLE ROW LEVEL SECURITY;
ALTER TABLE exp009.company_ner_links ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON exp009.company_ner_cache,exp009.company_ner_inputs,exp009.company_ner_links FROM PUBLIC;
-- Supabase API grants inherited from project defaults are removed if present.
DO $$ DECLARE r text; BEGIN
 FOREACH r IN ARRAY ARRAY['anon','authenticated','service_role'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('REVOKE ALL ON exp009.company_ner_cache,exp009.company_ner_inputs,exp009.company_ner_links FROM %I',r);
  END IF;
 END LOOP;
END $$;
-- Existing backend access only to the new tables; no public/frontend policies.
GRANT SELECT,INSERT,UPDATE ON exp009.company_ner_cache TO postgres;
GRANT SELECT,INSERT ON exp009.company_ner_inputs,exp009.company_ner_links TO postgres;
RESET ROLE;
REVOKE exp009_owner FROM CURRENT_USER GRANTED BY CURRENT_USER;
DO $verify$ BEGIN
 IF current_user<>'postgres' OR EXISTS(SELECT 1 FROM pg_auth_members a
   JOIN pg_roles r ON r.oid=a.roleid WHERE r.rolname='exp009_owner'
   AND a.member=(SELECT oid FROM pg_roles WHERE rolname=current_user) AND a.grantor=a.member)
 OR NOT EXISTS(SELECT 1 FROM pg_auth_members a JOIN pg_roles r ON r.oid=a.roleid
   JOIN pg_roles m ON m.oid=a.member JOIN pg_roles g ON g.oid=a.grantor
   WHERE r.rolname='exp009_owner' AND m.rolname=current_user AND g.rolname='supabase_admin'
     AND a.admin_option AND NOT a.inherit_option AND NOT a.set_option)
 THEN RAISE EXCEPTION 'NER_OWNER_FIX_RESTORE'; END IF;
END $verify$;
COMMIT;
