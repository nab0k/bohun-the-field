-- READ ONLY. Check only, no database writes.
SELECT id, name, source_type, country_code, active
FROM intelligence.sources WHERE name='UK Find a Tender (OCDS)';
SELECT COUNT(*) AS existing_uk_items
FROM intelligence.items i JOIN intelligence.sources s ON i.source_id=s.id
WHERE s.name='UK Find a Tender (OCDS)';
SELECT n.nspname,c.relname,c.relrowsecurity
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='intelligence' AND c.relname IN ('items','sources','item_changes');
