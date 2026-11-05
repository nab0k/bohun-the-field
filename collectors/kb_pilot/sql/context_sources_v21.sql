-- EXP-032 / C3-11 (D-20261105-C3-11-CONTEXT-SOURCES-A) — v21: the source of each context line.
-- Additive: one column with a default, so every existing writer keeps working; a new export function v3
-- (v2 stays for the current site until it switches). sources keeps its own rule of 1..5 sources of the material.
ALTER TABLE knowledge.synthesis_version
  ADD COLUMN context_sources jsonb NOT NULL DEFAULT '[]'::jsonb,
  ADD CONSTRAINT synthesis_version_context_sources_check
    CHECK (jsonb_typeof(context_sources) = 'array' AND jsonb_array_length(context_sources) <= 40);

CREATE FUNCTION knowledge.export_synthesis_feed_v3(p_limit integer DEFAULT 50)
RETURNS TABLE(synthesis_id bigint, headline text, what_happened text, context text, policy_direction text,
              bigger_picture text, supply_chain text, why_it_matters text, sources jsonb, event_type text,
              occurred_on date, first_published_at timestamptz, published_at timestamptz, topics jsonb,
              parent_synthesis_id bigint, confirmed_companies jsonb, label text,
              place_label text, place_lat double precision, place_lon double precision, context_sources jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'pg_catalog'
AS $function$
  SELECT f.*, v.context_sources
    FROM knowledge.export_synthesis_feed_v2(p_limit) f
    JOIN knowledge.synthesis s ON s.id = f.synthesis_id
    JOIN knowledge.synthesis_version v ON v.synthesis_id = s.id AND v.version = s.active_version
$function$;
REVOKE ALL ON FUNCTION knowledge.export_synthesis_feed_v3(integer) FROM PUBLIC, anon, authenticated;

CREATE FUNCTION public.export_synthesis_feed_v3(p_limit integer DEFAULT 50)
RETURNS TABLE(synthesis_id bigint, headline text, what_happened text, context text, policy_direction text,
              bigger_picture text, supply_chain text, why_it_matters text, sources jsonb, event_type text,
              occurred_on date, first_published_at timestamptz, published_at timestamptz, topics jsonb,
              parent_synthesis_id bigint, confirmed_companies jsonb, label text,
              place_label text, place_lat double precision, place_lon double precision, context_sources jsonb)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'pg_catalog'
AS $function$
  SELECT * FROM knowledge.export_synthesis_feed_v3(p_limit)
$function$;
REVOKE ALL ON FUNCTION public.export_synthesis_feed_v3(integer) FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION public.export_synthesis_feed_v3(integer) TO anon;

-- Rollback (separate decision):
--   DROP FUNCTION public.export_synthesis_feed_v3(integer); DROP FUNCTION knowledge.export_synthesis_feed_v3(integer);
--   ALTER TABLE knowledge.synthesis_version DROP CONSTRAINT synthesis_version_context_sources_check,
--     DROP COLUMN context_sources;
