-- EXP-028 DESIGN_v2_STEP2 — one-time transfer of the v16 dossier into node/edge (INSERT only; v16 unchanged).
-- Run once in one transaction after market_model_v18.sql.
CREATE FUNCTION pg_temp.nk(t text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE WHEN k ~ '^[a-z0-9][a-z0-9 -]{1,159}$' THEN k ELSE 'x-' || left(md5(coalesce(t, '')), 10) END
    FROM (SELECT btrim(left(btrim(regexp_replace(regexp_replace(lower(coalesce(t, '')), '[^a-z0-9-]+', ' ', 'g'),
                                                 '\s+', ' ', 'g')), 160)) AS k) s $$;

-- Programmes; names that are news headlines (a failed grouping, run 40923499569) are kept as suspect.
INSERT INTO knowledge.node (kind, key, name, aliases, topic_id, summary, status, legacy_programme_id, created_at, updated_at)
SELECT 'programme', p.key, p.name, p.aliases, p.topic_id, p.summary,
       CASE WHEN EXISTS (SELECT 1 FROM intelligence.items i WHERE lower(btrim(i.title)) = lower(btrim(p.name)))
            THEN 'suspect' ELSE 'candidate' END,
       p.id, p.created_at, p.updated_at
  FROM knowledge.programme p ORDER BY p.id;

INSERT INTO knowledge.node_document (node_id, document_id)
SELECT n.id, pd.document_id FROM knowledge.programme_document pd JOIN knowledge.node n ON n.legacy_programme_id = pd.programme_id;

-- Supply-chain sides: upper is a company; lower by level (material / technology / otherwise company).
INSERT INTO knowledge.node (kind, key, name)
SELECT DISTINCT ON (kind, pg_temp.nk(name)) kind, pg_temp.nk(name), name FROM (
  SELECT 'company' AS kind, btrim(row_data->>'upper') AS name FROM knowledge.dossier_entry WHERE kind = 'supply_chain'
  UNION ALL
  SELECT CASE WHEN row_data->>'level' IN ('material', 'raw_material') THEN 'material'
              WHEN row_data->>'level' = 'technology' THEN 'technology' ELSE 'company' END,
         btrim(row_data->>'lower') FROM knowledge.dossier_entry WHERE kind = 'supply_chain') s
 WHERE coalesce(name, '') <> '' ORDER BY kind, pg_temp.nk(name), name
ON CONFLICT (kind, key) DO NOTHING;

-- Restriction regimes.
INSERT INTO knowledge.node (kind, key, name)
SELECT DISTINCT ON (pg_temp.nk(name)) 'regulation', pg_temp.nk(name), name FROM (
  SELECT coalesce(nullif(btrim(row_data->>'subject'), ''),
                  btrim(coalesce(row_data->>'issuer', '') || ' ' || coalesce(row_data->>'kind', ''))) AS name
    FROM knowledge.dossier_entry WHERE kind = 'restriction') s
 WHERE coalesce(name, '') <> '' ORDER BY pg_temp.nk(name), name
ON CONFLICT (kind, key) DO NOTHING;

INSERT INTO knowledge.edge (src_node_id, rel, dst_node_id, context_node_id, polarity, occurred_on, date_precision, valid_to,
                            amount, currency, unit, commitment, what, source_url, document_id, anchor, item_id,
                            origin_item_id, status, row_data, row_hash, extractor, run_ref, legacy_dossier_entry_id, created_at)
SELECT CASE d.kind WHEN 'chronology' THEN p.id
                   WHEN 'supply_chain' THEN coalesce(lo.id, p.id)
                   ELSE coalesce(rg.id, p.id) END,
       CASE d.kind WHEN 'chronology' THEN 'milestone' WHEN 'supply_chain' THEN 'supplies' ELSE 'restricts' END,
       CASE d.kind WHEN 'supply_chain' THEN up.id WHEN 'restriction' THEN p.id END,
       p.id,
       CASE WHEN d.commitment = 'suspension' THEN 'negative' ELSE 'positive' END,
       d.entry_date, d.date_precision,
       CASE WHEN d.row_data->>'ends_on' ~ '^\d{4}-\d{2}-\d{2}$' THEN (d.row_data->>'ends_on')::date END,
       d.amount, d.currency,
       CASE WHEN d.kind = 'supply_chain' THEN d.row_data->>'level' END,
       d.commitment, d.what, d.source_url, d.document_id, d.anchor, d.archive_item_id,
       (SELECT e.origin_item_id FROM knowledge.item_embedding e WHERE e.item_id = d.archive_item_id),
       'verified', d.row_data, d.row_hash, 'v16:' || d.pilot_version, d.run_ref, d.id, d.created_at
  FROM knowledge.dossier_entry d
  JOIN knowledge.node p ON p.legacy_programme_id = d.programme_id
  LEFT JOIN knowledge.node up ON d.kind = 'supply_chain' AND up.kind = 'company'
        AND up.key = pg_temp.nk(btrim(d.row_data->>'upper')) AND coalesce(btrim(d.row_data->>'upper'), '') <> ''
  LEFT JOIN knowledge.node lo ON d.kind = 'supply_chain' AND coalesce(btrim(d.row_data->>'lower'), '') <> ''
        AND lo.key = pg_temp.nk(btrim(d.row_data->>'lower'))
        AND lo.kind = CASE WHEN d.row_data->>'level' IN ('material', 'raw_material') THEN 'material'
                           WHEN d.row_data->>'level' = 'technology' THEN 'technology' ELSE 'company' END
  LEFT JOIN knowledge.node rg ON d.kind = 'restriction' AND rg.kind = 'regulation'
        AND rg.key = pg_temp.nk(coalesce(nullif(btrim(d.row_data->>'subject'), ''),
                       btrim(coalesce(d.row_data->>'issuer', '') || ' ' || coalesce(d.row_data->>'kind', ''))))
 WHERE d.verify_status = 'ok'
 ORDER BY d.id;
