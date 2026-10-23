-- REVIEW: Supabase intelligence.sources.id is GENERATED ALWAYS AS IDENTITY.
-- Do not hardcode ID; identity sequence allocates it.
BEGIN;
SELECT pg_advisory_xact_lock(hashtext('bohun:borme:source-registration'));
DO $$
BEGIN
 IF EXISTS (SELECT 1 FROM intelligence.sources WHERE lower(btrim(name))='borme') THEN
  RAISE EXCEPTION 'BORME source already exists: review existing ID';
 END IF;
END $$;
INSERT INTO intelligence.sources(name,url,source_type,country_code,collection_method,active)
VALUES ('BORME','https://www.boe.es/borme/','registry','ES','official-publication',true)
RETURNING id,name,url,source_type,country_code,collection_method,active;
COMMIT;
