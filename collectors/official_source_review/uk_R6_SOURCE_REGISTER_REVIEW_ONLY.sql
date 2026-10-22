-- REVIEW ONLY, NOT EXECUTED. Separate owner approval required.
-- UK source row is absent as of the read-only check on 2026-10-21.
-- Do not execute before all consumer/publication gates have been audited.
INSERT INTO intelligence.sources
  (name, url, source_type, country_code, collection_method, active)
SELECT 'UK Find a Tender (OCDS)', 'https://www.find-tender.service.gov.uk/',
       'procurement', 'GB', 'official_api', true
WHERE NOT EXISTS (
  SELECT 1 FROM intelligence.sources
  WHERE name = 'UK Find a Tender (OCDS)'
)
RETURNING id;
