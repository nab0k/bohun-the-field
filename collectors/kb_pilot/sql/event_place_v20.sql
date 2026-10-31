-- EXP-028 D-20261030-MAP-FEED — v20: place of an event for the map; feed export with coordinates.
-- Additive: three nullable columns; a new export function v2 (v1 stays for the current site until it switches).
ALTER TABLE knowledge.event
  ADD COLUMN place_label text,
  ADD COLUMN place_lat double precision CHECK (place_lat BETWEEN -90 AND 90),
  ADD COLUMN place_lon double precision CHECK (place_lon BETWEEN -180 AND 180),
  ADD CONSTRAINT event_place_pair CHECK ((place_lat IS NULL) = (place_lon IS NULL));

CREATE FUNCTION knowledge.export_synthesis_feed_v2(p_limit integer DEFAULT 50)
RETURNS TABLE(synthesis_id bigint, headline text, what_happened text, context text, policy_direction text,
              bigger_picture text, supply_chain text, why_it_matters text, sources jsonb, event_type text,
              occurred_on date, first_published_at timestamptz, published_at timestamptz, topics jsonb,
              parent_synthesis_id bigint, confirmed_companies jsonb, label text,
              place_label text, place_lat double precision, place_lon double precision)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'pg_catalog'
AS $function$
  SELECT f.*, e.place_label, e.place_lat, e.place_lon
    FROM knowledge.export_synthesis_feed(p_limit) f
    JOIN knowledge.synthesis s ON s.id = f.synthesis_id
    JOIN knowledge.event e ON e.id = s.event_id
$function$;
REVOKE ALL ON FUNCTION knowledge.export_synthesis_feed_v2(integer) FROM PUBLIC, anon, authenticated;

CREATE FUNCTION public.export_synthesis_feed_v2(p_limit integer DEFAULT 50)
RETURNS TABLE(synthesis_id bigint, headline text, what_happened text, context text, policy_direction text,
              bigger_picture text, supply_chain text, why_it_matters text, sources jsonb, event_type text,
              occurred_on date, first_published_at timestamptz, published_at timestamptz, topics jsonb,
              parent_synthesis_id bigint, confirmed_companies jsonb, label text,
              place_label text, place_lat double precision, place_lon double precision)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'pg_catalog'
AS $function$
  SELECT * FROM knowledge.export_synthesis_feed_v2(p_limit)
$function$;
REVOKE ALL ON FUNCTION public.export_synthesis_feed_v2(integer) FROM PUBLIC, authenticated;
GRANT EXECUTE ON FUNCTION public.export_synthesis_feed_v2(integer) TO anon;
