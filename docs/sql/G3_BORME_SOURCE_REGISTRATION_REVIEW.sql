-- REVIEW ONLY. Do not execute before approval and backup.
-- Supabase intelligence.sources.id is bigint without a column default.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('bohun:borme:source-registration'));
DO $$
DECLARE v_id bigint;
BEGIN
 IF EXISTS (SELECT 1 FROM intelligence.sources WHERE lower(btrim(name))='borme') THEN
  RAISE EXCEPTION 'BORME source already exists: review existing ID';
 END IF;
 SELECT coalesce(max(id),0)+1 INTO v_id FROM intelligence.sources;
 INSERT INTO intelligence.sources(id,name,url,source_type,country_code,collection_method,active)
 VALUES (v_id,'BORME','https://www.boe.es/borme/','registry','ES','official-publication',true);
 RAISE NOTICE 'BORME source ID: %',v_id;
END $$;
COMMIT;
-- Read actual ID after approved deployment:
-- SELECT id,name,url FROM intelligence.sources WHERE name='BORME';
-- DO NOT hardcode an unverified source ID in production RLS.
