-- EXP-028 DESIGN_v2_STEP3 — v19: extraction log and edge support (D-20261030-EXP028-V2-STEP3).
-- Additive: new closed objects in schema knowledge.
CREATE TABLE knowledge.item_extraction (
  item_id      bigint NOT NULL REFERENCES intelligence.items(id),
  extractor    text NOT NULL,
  outcome      text NOT NULL CHECK (outcome IN ('done','no_relations','skipped_reprint','error')),
  provider     text,
  written      integer NOT NULL DEFAULT 0,
  dropped      jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(dropped) = 'object'),  -- reason → count
  error        text,
  run_ref      text,
  processed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (item_id, extractor)
);
ALTER TABLE knowledge.item_extraction ENABLE ROW LEVEL SECURITY;

-- How many independent first sources state the same relation (same ends, rel and date); counted, never edited in.
CREATE VIEW knowledge.edge_support WITH (security_invoker = true) AS
SELECT src_node_id, rel, dst_node_id, occurred_on,
       count(DISTINCT coalesce(origin_item_id, item_id)) FILTER (WHERE coalesce(origin_item_id, item_id) IS NOT NULL)
         AS independent_sources,
       count(DISTINCT document_id) AS documents,
       array_agg(id ORDER BY id) AS edge_ids
  FROM knowledge.edge
 WHERE superseded_by IS NULL
 GROUP BY src_node_id, rel, dst_node_id, occurred_on;

REVOKE ALL ON knowledge.item_extraction, knowledge.edge_support FROM PUBLIC, anon, authenticated;

-- A grouping without a programme saved its rows under the name "other" (run 41041653349): not a node.
UPDATE knowledge.node SET status = 'suspect', updated_at = now()
 WHERE kind = 'programme' AND key IN ('other', 'unknown', 'none', 'n a', 'na', 'programme', 'program');
