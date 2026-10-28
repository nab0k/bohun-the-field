-- LOCAL CANDIDATE ONLY. Not applied; schema/credentials/cloud require approval.
-- PostgreSQL >=14; private direct-DB API, not a PostgREST/public RPC.
-- One-time migration: collisions abort instead of adopting existing objects.
BEGIN;
SET LOCAL search_path = pg_catalog, pg_temp;
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '30s';
DO $preflight$
BEGIN
  IF current_setting('server_version_num')::integer < 140000
     OR current_setting('server_encoding') <> 'UTF8'
     OR EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'exp009')
     OR EXISTS (SELECT 1 FROM pg_roles WHERE rolname IN ('exp009_owner','bohun_intake')) THEN
    RAISE EXCEPTION 'INTAKE_MIGRATION_PREFLIGHT_FAILED';
  END IF;
  IF (SELECT count(*) FROM intelligence.sources WHERE name = 'Inoreader BOHUN') <> 1
     OR NOT EXISTS (SELECT 1 FROM intelligence.sources WHERE id = 2 AND name = 'Inoreader BOHUN') THEN
    RAISE EXCEPTION 'INTAKE_SOURCE_IDENTITY_CHANGED';
  END IF;
END
$preflight$;

CREATE ROLE exp009_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- LOGIN/password provisioning is deliberately a later, separately approved step.
CREATE ROLE bohun_intake NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT exp009_owner TO CURRENT_USER;
CREATE SCHEMA exp009 AUTHORIZATION exp009_owner;
REVOKE ALL ON SCHEMA exp009 FROM PUBLIC, anon, authenticated, service_role;
GRANT USAGE ON SCHEMA intelligence TO exp009_owner;
GRANT SELECT (id, name), REFERENCES (id) ON intelligence.sources TO exp009_owner;
GRANT SELECT (id, source_id, external_id), REFERENCES (id) ON intelligence.items TO exp009_owner;

SET LOCAL ROLE exp009_owner;
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;

CREATE TABLE exp009.intake_batches (
  batch_id uuid PRIMARY KEY,
  contract_version text NOT NULL CHECK (contract_version = 'inoreader-artifact-v1'),
  pipeline_version text NOT NULL CHECK (pipeline_version ~ '^[a-z][a-z0-9_.-]{0,63}$'),
  producer_sha text NOT NULL CHECK (producer_sha ~ '^[0-9a-f]{40}$'),
  source_id bigint NOT NULL REFERENCES intelligence.sources(id) CHECK (source_id = 2),
  collected_at timestamptz NOT NULL,
  stored_at timestamptz NOT NULL DEFAULT now(),
  outcome text NOT NULL CHECK (outcome IN ('STORED','EMPTY')),
  item_count integer NOT NULL CHECK (item_count BETWEEN 0 AND 5),
  payload_bytes integer NOT NULL CHECK (payload_bytes BETWEEN 0 AND 6291456),
  manifest_json text NOT NULL CHECK (octet_length(convert_to(manifest_json,'UTF8')) <= 32768),
  manifest_sha256 text NOT NULL CHECK (
    manifest_sha256 ~ '^[0-9a-f]{64}$' AND
    manifest_sha256 = encode(sha256(convert_to(manifest_json,'UTF8')),'hex')),
  CHECK ((item_count = 0 AND outcome = 'EMPTY') OR (item_count > 0 AND outcome = 'STORED'))
);
CREATE TABLE exp009.artifacts (
  artifact_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  source_id bigint NOT NULL REFERENCES intelligence.sources(id) CHECK (source_id = 2),
  external_id text NOT NULL CHECK (btrim(external_id) <> '' AND octet_length(external_id) <= 2048),
  contract_version text NOT NULL CHECK (contract_version = 'inoreader-artifact-v1'),
  payload_json text NOT NULL,
  payload_sha256 text NOT NULL CHECK (
    payload_sha256 ~ '^[0-9a-f]{64}$' AND
    payload_sha256 = encode(sha256(convert_to(payload_json,'UTF8')),'hex')),
  payload_bytes integer NOT NULL CHECK (payload_bytes BETWEEN 1 AND 6291456 AND
    payload_bytes = octet_length(convert_to(payload_json,'UTF8'))),
  capture_status text NOT NULL CHECK (capture_status IN ('METADATA_ONLY',
    'BODY_PRESENT_COMPLETENESS_UNVERIFIED','EMPTY_TEXT','UNSUPPORTED_SHAPE',
    'TOO_LARGE','CAPTURE_BUDGET_EXHAUSTED')),
  first_received_at timestamptz NOT NULL,
  UNIQUE (source_id,external_id,contract_version,payload_sha256)
);
CREATE TABLE exp009.intake_batch_items (
  batch_id uuid NOT NULL REFERENCES exp009.intake_batches(batch_id),
  ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 4),
  artifact_id uuid NOT NULL REFERENCES exp009.artifacts(artifact_id),
  external_id text NOT NULL,
  received_at timestamptz NOT NULL,
  existing_item_id bigint REFERENCES intelligence.items(id),
  PRIMARY KEY (batch_id,ordinal),
  UNIQUE (batch_id,artifact_id),
  UNIQUE (batch_id,external_id)
);
CREATE TABLE exp009.jobs (
  job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  input_artifact_id uuid NOT NULL REFERENCES exp009.artifacts(artifact_id),
  stage text NOT NULL CHECK (stage = 'intake_validate'),
  pipeline_version text NOT NULL CHECK (pipeline_version ~ '^[a-z][a-z0-9_.-]{0,63}$'),
  state text NOT NULL CHECK (state = 'queued'),
  attempts integer NOT NULL DEFAULT 0 CHECK (attempts = 0),
  not_before timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (input_artifact_id,stage,pipeline_version)
);
CREATE INDEX ON exp009.intake_batches(source_id);
CREATE INDEX ON exp009.intake_batch_items(artifact_id);
CREATE INDEX ON exp009.intake_batch_items(existing_item_id);
CREATE INDEX ON exp009.jobs(not_before,created_at,job_id) WHERE state = 'queued';
ALTER TABLE exp009.intake_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE exp009.artifacts ENABLE ROW LEVEL SECURITY;
ALTER TABLE exp009.intake_batch_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE exp009.jobs ENABLE ROW LEVEL SECURITY;

-- Helpers are INVOKER, private and not granted to the runtime.
CREATE FUNCTION exp009._keys(p jsonb, names text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT jsonb_typeof(p) = 'object' AND p ?& names AND (p - names) = '{}'::jsonb
$$;

CREATE FUNCTION exp009._payload(p_text text) RETURNS jsonb
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  p jsonb; i jsonb; r jsonb; c jsonb; k text; retained boolean;
BEGIN
  IF p_text IS NULL OR octet_length(convert_to(p_text,'UTF8')) NOT BETWEEN 1 AND 6291456 THEN
    RAISE EXCEPTION 'INTAKE_PAYLOAD_SIZE' USING ERRCODE = '22023';
  END IF;
  p := p_text::jsonb; i := p->'item'; r := i->'raw_data'; c := r->'content_capture';
  IF (exp009._keys(p,ARRAY['contract_version','source_id','item']) AND
      p->>'contract_version' = 'inoreader-artifact-v1' AND p->'source_id' = '2'::jsonb AND
      exp009._keys(i,ARRAY['external_id','title','url','published_at','raw_data']) AND
      jsonb_typeof(i->'external_id') = 'string' AND btrim(i->>'external_id') <> '' AND
      octet_length(i->>'external_id') <= 2048 AND jsonb_typeof(i->'title') = 'string' AND
      char_length(i->>'title') <= 2000 AND jsonb_typeof(i->'url') IN ('string','null') AND
      (i->'published_at' = 'null'::jsonb OR (jsonb_typeof(i->'published_at') = 'string' AND
        i->>'published_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00$')) AND
      exp009._keys(r,ARRAY['inoreader_id','folder','origin_title','origin_stream_id',
                          'published','url','title','content_capture']) AND
      r->'inoreader_id' = i->'external_id' AND jsonb_typeof(r->'folder') = 'string' AND
      left(r->>'folder',6) = 'BOHUN_' AND jsonb_typeof(r->'title') = 'string' AND
      left(r->>'title',2000) = i->>'title' AND r->'url' = i->'url' AND
      jsonb_typeof(r->'origin_title') IN ('string','null') AND
      jsonb_typeof(r->'origin_stream_id') IN ('string','null') AND
      jsonb_typeof(r->'published') IN ('number','null') AND
      exp009._keys(c,ARRAY['version','status','provider_field','text_extraction_version',
        'raw_html','raw_html_sha256','raw_html_bytes','text','text_sha256','text_bytes',
        'text_characters','completeness','issues']) AND c->'version' = '1'::jsonb AND
      c->>'provider_field' = 'summary.content' AND
      c->>'text_extraction_version' = 'htmlparser-text-v1' AND c->>'completeness' = 'UNKNOWN' AND
      c->>'status' IN ('METADATA_ONLY','BODY_PRESENT_COMPLETENESS_UNVERIFIED','EMPTY_TEXT',
                     'UNSUPPORTED_SHAPE','TOO_LARGE','CAPTURE_BUDGET_EXHAUSTED') AND
      jsonb_typeof(c->'issues') = 'array' AND jsonb_array_length(c->'issues') <= 1) IS NOT TRUE THEN
    RAISE EXCEPTION 'INTAKE_PAYLOAD_INVALID' USING ERRCODE = '22023';
  END IF;
  IF i->'published_at' <> 'null'::jsonb THEN PERFORM (i->>'published_at')::timestamptz; END IF;
  FOREACH k IN ARRAY ARRAY['raw_html_bytes','text_bytes','text_characters'] LOOP
    IF (jsonb_typeof(c->k) = 'number' AND c->>k ~ '^[0-9]+$') IS NOT TRUE THEN
      RAISE EXCEPTION 'INTAKE_CAPTURE_COUNTER_INVALID' USING ERRCODE = '22023';
    END IF;
    PERFORM (c->>k)::bigint;
  END LOOP;
  retained := c->>'status' IN ('BODY_PRESENT_COMPLETENESS_UNVERIFIED','EMPTY_TEXT');
  IF retained THEN
    IF (jsonb_typeof(c->'raw_html') = 'string' AND btrim(c->>'raw_html') <> '' AND
        jsonb_typeof(c->'text') = 'string' AND
        octet_length(convert_to(c->>'raw_html','UTF8')) = (c->>'raw_html_bytes')::bigint AND
        (c->>'raw_html_bytes')::bigint <= 2097152 AND
        octet_length(convert_to(c->>'text','UTF8')) = (c->>'text_bytes')::bigint AND
        (c->>'text_bytes')::bigint <= 131072 AND
        char_length(c->>'text') = (c->>'text_characters')::bigint AND
        c->>'raw_html_sha256' = encode(sha256(convert_to(c->>'raw_html','UTF8')),'hex') AND
        c->>'text_sha256' = encode(sha256(convert_to(c->>'text','UTF8')),'hex') AND
        ((c->>'status' = 'BODY_PRESENT_COMPLETENESS_UNVERIFIED' AND c->>'text' <> '' AND c->'issues' = '[]'::jsonb)
         OR (c->>'status' = 'EMPTY_TEXT' AND c->>'text' = '' AND c->'issues' = '["NO_EXTRACTED_TEXT"]'::jsonb))) IS NOT TRUE THEN
      RAISE EXCEPTION 'INTAKE_BODY_INVALID' USING ERRCODE = '22023';
    END IF;
  ELSIF (c->'raw_html' = 'null'::jsonb AND c->'text' = 'null'::jsonb AND
         c->'raw_html_sha256' = 'null'::jsonb AND c->'text_sha256' = 'null'::jsonb AND
         c->'text_characters' = '0'::jsonb AND jsonb_typeof(c->'issues'->0) = 'string' AND
         ((c->>'status' = 'METADATA_ONLY' AND c->'issues'->>0 = 'NO_BODY') OR
          (c->>'status' = 'UNSUPPORTED_SHAPE' AND c->'issues'->>0 IN
             ('ITEM_NOT_OBJECT','SUMMARY_NOT_OBJECT','BODY_NOT_STRING','INVALID_UTF8')) OR
          (c->>'status' = 'TOO_LARGE' AND c->'issues'->>0 IN ('HTML_LIMIT_EXCEEDED','TEXT_LIMIT_EXCEEDED')) OR
          (c->>'status' = 'CAPTURE_BUDGET_EXHAUSTED' AND c->'issues'->>0 = 'CAPTURE_BUDGET_EXHAUSTED'))) IS NOT TRUE THEN
    RAISE EXCEPTION 'INTAKE_BODYLESS_INVALID' USING ERRCODE = '22023';
  END IF;
  RETURN p;
END $$;

-- Exact compact JSON byte size: fixed capture fields are scalars or an issues
-- array of zero/one string (no array separator whitespace). Object key order is
-- irrelevant to length. Never hash a reserialized jsonb representation.
CREATE FUNCTION exp009._capture_bytes(c jsonb, received text) RETURNS bigint
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp AS $$
  SELECT 2 + count(*) - 1 + sum(octet_length(convert_to(to_json(key)::text,'UTF8')) + 1 +
                                octet_length(convert_to(value::text,'UTF8')))
  FROM jsonb_each(c || jsonb_build_object('captured_at',received))
$$;

CREATE FUNCTION exp009.read_intake(p_id uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT jsonb_build_object('batch_id',b.batch_id,'manifest_json',b.manifest_json,
    'manifest_sha256',b.manifest_sha256,'items',coalesce((
      SELECT jsonb_agg(jsonb_build_object('ordinal',m.ordinal,'artifact_id',m.artifact_id,
        'job_id',j.job_id,'payload_sha256',a.payload_sha256) ORDER BY m.ordinal)
      FROM exp009.intake_batch_items m
      JOIN exp009.artifacts a ON a.artifact_id = m.artifact_id
      JOIN exp009.jobs j ON j.input_artifact_id = a.artifact_id
        AND j.stage = 'intake_validate' AND j.pipeline_version = b.pipeline_version
      WHERE m.batch_id = b.batch_id),'[]'::jsonb))
  FROM exp009.intake_batches b WHERE b.batch_id = p_id
$$;

CREATE FUNCTION exp009.read_artifact(p_id uuid, p_hash text) RETURNS text
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT a.payload_json FROM exp009.artifacts a
  WHERE a.artifact_id = p_id AND a.payload_sha256 = p_hash
$$;

CREATE FUNCTION exp009.persist_intake(p_manifest text, p_hash text, p_payloads text[]) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  m jsonb; e jsonb; p jsonb; c jsonb; v_parsed jsonb[] := ARRAY[]::jsonb[];
  v_i integer; v_count integer; v_bytes bigint := 0; v_capture bigint := 0;
  v_id uuid; v_artifact uuid; v_existing text; v_seen text[] := ARRAY[]::text[];
BEGIN
  IF p_manifest IS NULL OR p_hash IS NULL OR p_payloads IS NULL OR
     octet_length(convert_to(p_manifest,'UTF8')) > 32768 OR
     p_hash !~ '^[0-9a-f]{64}$' OR p_hash <> encode(sha256(convert_to(p_manifest,'UTF8')),'hex') OR
     cardinality(p_payloads) > 5 OR (cardinality(p_payloads) > 0 AND
       (array_ndims(p_payloads) <> 1 OR array_lower(p_payloads,1) <> 1)) THEN
    RAISE EXCEPTION 'INTAKE_MANIFEST_INVALID' USING ERRCODE = '22023';
  END IF;
  m := p_manifest::jsonb;
  IF (exp009._keys(m,ARRAY['batch_id','contract_version','pipeline_version','producer_sha',
      'source_id','collected_at','outcome','item_count','payload_bytes','capture_bytes','items']) AND
      m->>'contract_version' = 'inoreader-artifact-v1' AND m->'source_id' = '2'::jsonb AND
      m->>'pipeline_version' ~ '^[a-z][a-z0-9_.-]{0,63}$' AND m->>'producer_sha' ~ '^[0-9a-f]{40}$' AND
      m->>'collected_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00$' AND
      jsonb_typeof(m->'items') = 'array' AND m->>'item_count' ~ '^[0-5]$' AND
      m->>'payload_bytes' ~ '^[0-9]+$' AND m->>'capture_bytes' ~ '^[0-9]+$') IS NOT TRUE THEN
    RAISE EXCEPTION 'INTAKE_MANIFEST_INVALID' USING ERRCODE = '22023';
  END IF;
  v_id := (m->>'batch_id')::uuid; v_count := (m->>'item_count')::integer;
  PERFORM (m->>'collected_at')::timestamptz;
  IF v_count <> cardinality(p_payloads) OR v_count <> jsonb_array_length(m->'items') OR
     (m->>'outcome' = CASE WHEN v_count = 0 THEN 'EMPTY' ELSE 'STORED' END) IS NOT TRUE THEN
    RAISE EXCEPTION 'INTAKE_MANIFEST_COUNT_INVALID' USING ERRCODE = '22023';
  END IF;
  -- Validate all input before the first INSERT; runtime cannot write tables.
  FOR v_i IN 1..v_count LOOP
    e := m->'items'->(v_i-1); p := exp009._payload(p_payloads[v_i]);
    c := p->'item'->'raw_data'->'content_capture';
    IF (exp009._keys(e,ARRAY['ordinal','external_id','payload_sha256','received_at','existing_item_id']) AND
        e->'ordinal' = to_jsonb(v_i-1) AND e->'external_id' = p->'item'->'external_id' AND
        e->>'payload_sha256' = encode(sha256(convert_to(p_payloads[v_i],'UTF8')),'hex') AND
        e->>'received_at' ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6}\+00:00$' AND
        (e->'existing_item_id' = 'null'::jsonb OR
          (jsonb_typeof(e->'existing_item_id') = 'number' AND e->>'existing_item_id' ~ '^[1-9][0-9]*$'))) IS NOT TRUE
        OR e->>'external_id' = ANY(v_seen) THEN
      RAISE EXCEPTION 'INTAKE_MEMBER_INVALID' USING ERRCODE = '22023';
    END IF;
    PERFORM (e->>'received_at')::timestamptz;
    -- Existing intelligence RLS is NOT broadened. A supplied link must be
    -- visible and match all three keys, or is rejected. Initial CLI uses NULL.
    IF e->'existing_item_id' <> 'null'::jsonb AND NOT EXISTS (
       SELECT 1 FROM intelligence.items i WHERE i.id = (e->>'existing_item_id')::bigint
         AND i.source_id = 2 AND i.external_id = e->>'external_id') THEN
      RAISE EXCEPTION 'INTAKE_METADATA_LINK_UNVERIFIED' USING ERRCODE = '22023';
    END IF;
    v_seen := array_append(v_seen,e->>'external_id');
    v_parsed := array_append(v_parsed,p);
    v_bytes := v_bytes + octet_length(convert_to(p_payloads[v_i],'UTF8'));
    v_capture := v_capture + exp009._capture_bytes(c,e->>'received_at');
  END LOOP;
  IF v_bytes > 6291456 OR v_capture > 5242880 OR
     v_bytes <> (m->>'payload_bytes')::bigint OR v_capture <> (m->>'capture_bytes')::bigint THEN
    RAISE EXCEPTION 'INTAKE_BATCH_SIZE_INVALID' USING ERRCODE = '22023';
  END IF;
  INSERT INTO exp009.intake_batches(batch_id,contract_version,pipeline_version,producer_sha,
    source_id,collected_at,outcome,item_count,payload_bytes,manifest_json,manifest_sha256)
  VALUES(v_id,m->>'contract_version',m->>'pipeline_version',m->>'producer_sha',2,
    (m->>'collected_at')::timestamptz,m->>'outcome',v_count,v_bytes::integer,p_manifest,p_hash)
  ON CONFLICT (batch_id) DO NOTHING;
  IF NOT FOUND THEN
    SELECT b.manifest_json INTO v_existing FROM exp009.intake_batches b WHERE b.batch_id = v_id;
    IF v_existing IS DISTINCT FROM p_manifest THEN
      RAISE EXCEPTION 'INTAKE_BATCH_ID_CONFLICT' USING ERRCODE = '40001';
    END IF;
    RETURN exp009.read_intake(v_id);
  END IF;
  -- Consistent artifact lock ordering across batches, independent of feed order.
  FOR v_i IN SELECT s FROM generate_subscripts(p_payloads,1) AS positions(s)
            ORDER BY (m->'items'->(s-1)->>'external_id'), (m->'items'->(s-1)->>'payload_sha256') LOOP
    e := m->'items'->(v_i-1); p := v_parsed[v_i];
    INSERT INTO exp009.artifacts(source_id,external_id,contract_version,payload_json,
      payload_sha256,payload_bytes,capture_status,first_received_at)
    VALUES(2,e->>'external_id',m->>'contract_version',p_payloads[v_i],e->>'payload_sha256',
      octet_length(convert_to(p_payloads[v_i],'UTF8')),p->'item'->'raw_data'->'content_capture'->>'status',
      (e->>'received_at')::timestamptz)
    ON CONFLICT (source_id,external_id,contract_version,payload_sha256) DO NOTHING;
    SELECT a.artifact_id,a.payload_json INTO v_artifact,v_existing FROM exp009.artifacts a
      WHERE a.source_id = 2 AND a.external_id = e->>'external_id'
        AND a.contract_version = m->>'contract_version' AND a.payload_sha256 = e->>'payload_sha256';
    IF v_artifact IS NULL OR v_existing IS DISTINCT FROM p_payloads[v_i] THEN
      RAISE EXCEPTION 'INTAKE_ARTIFACT_IDENTITY_CONFLICT' USING ERRCODE = '40001';
    END IF;
    INSERT INTO exp009.intake_batch_items(batch_id,ordinal,artifact_id,external_id,received_at,existing_item_id)
    VALUES(v_id,v_i-1,v_artifact,e->>'external_id',(e->>'received_at')::timestamptz,
      (e->>'existing_item_id')::bigint);
    INSERT INTO exp009.jobs(input_artifact_id,stage,pipeline_version,state)
    VALUES(v_artifact,'intake_validate',m->>'pipeline_version','queued')
    ON CONFLICT (input_artifact_id,stage,pipeline_version) DO NOTHING;
  END LOOP;
  RETURN exp009.read_intake(v_id);
END $$;

-- Explicit revocation in this same transaction, even if defaults were altered.
REVOKE ALL ON ALL TABLES IN SCHEMA exp009 FROM PUBLIC, anon, authenticated, service_role, bohun_intake;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA exp009 FROM PUBLIC, anon, authenticated, service_role, bohun_intake;
GRANT USAGE ON SCHEMA exp009 TO bohun_intake;
GRANT EXECUTE ON FUNCTION exp009.persist_intake(text,text,text[]),
  exp009.read_intake(uuid), exp009.read_artifact(uuid,text) TO bohun_intake;
RESET ROLE;
REVOKE exp009_owner FROM CURRENT_USER;
DO $privileges$
BEGIN
  IF has_schema_privilege('bohun_intake','exp009','CREATE') OR
     has_table_privilege('bohun_intake','intelligence.items','INSERT,UPDATE,DELETE') OR
     has_table_privilege('bohun_intake','intelligence.sources','INSERT,UPDATE,DELETE') OR
     has_column_privilege('bohun_intake','intelligence.items','raw_data','SELECT') THEN
    RAISE EXCEPTION 'INTAKE_INHERITED_PRIVILEGE_CONFLICT';
  END IF;
END
$privileges$;
-- No existing policy/grant/function is modified. No exposure setting is changed.
-- Must separately verify ACLs, inherited PUBLIC privileges, PostgREST exclusion,
-- rollback procedure, SQL concurrency, runtime provisioning and read-back.
COMMIT;
