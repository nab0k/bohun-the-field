-- EXP-028 DESIGN_v2_STEP2 — market model v18: nodes, edges (observations), completeness checklist
-- (D-20261030-EXP028-V2-STEP2). Additive: new closed objects in schema knowledge; v16 tables unchanged.
CREATE TABLE knowledge.node (
  id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  kind                text NOT NULL CHECK (kind IN ('programme','product','company','org','technology','material',
                                                    'regulation','procurement','place')),
  key                 text NOT NULL CHECK (key ~ '^[a-z0-9][a-z0-9 -]{1,159}$'),  -- normalised name
  name                text NOT NULL,
  aliases             text[] NOT NULL DEFAULT '{}',
  wikidata_qid        text CHECK (wikidata_qid ~ '^Q[0-9]+$'),
  notion_company_id   text,                 -- card in Notion «Компании» (master); matched by A-26
  topic_id            bigint REFERENCES knowledge.topic(id),
  summary             text,
  embedding           extensions.halfvec(1024),
  status              text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','confirmed','suspect')),
  legacy_programme_id bigint UNIQUE REFERENCES knowledge.programme(id),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, key)
);
CREATE TABLE knowledge.node_document (
  node_id     bigint NOT NULL REFERENCES knowledge.node(id) ON DELETE CASCADE,
  document_id bigint NOT NULL REFERENCES knowledge.document(id) ON DELETE CASCADE,
  PRIMARY KEY (node_id, document_id)
);
-- An edge is an observation from a source; it is never edited back in time: a new version is a new row.
CREATE TABLE knowledge.edge (
  id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  src_node_id     bigint NOT NULL REFERENCES knowledge.node(id),
  rel             text NOT NULL CHECK (rel IN ('milestone','supplies','orders','funds','produces','depends_on',
                                               'restricts','regulates','part_of','competes','exports_to','price')),
  dst_node_id     bigint REFERENCES knowledge.node(id),
  context_node_id bigint REFERENCES knowledge.node(id),          -- usually the programme
  polarity        text NOT NULL DEFAULT 'positive' CHECK (polarity IN ('positive','negative')),
  occurred_on     date,
  date_precision  text CHECK (date_precision IN ('day','month','year')),
  valid_to        date,
  amount          numeric(20,2),
  currency        text,
  quantity        numeric,
  unit            text,
  commitment      text,
  what            text NOT NULL,
  quote           text,
  quote_verified  boolean,
  source_url      text,
  document_id     bigint REFERENCES knowledge.document(id),
  anchor          text,
  item_id         bigint REFERENCES intelligence.items(id),
  origin_item_id  bigint REFERENCES intelligence.items(id),      -- first source (item_embedding): reprints = one evidence
  status          text NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate','verified','confirmed')),
  row_data        jsonb NOT NULL DEFAULT '{}',
  row_hash        text NOT NULL CHECK (row_hash ~ '^[0-9a-f]{64}$'),
  extractor       text NOT NULL,
  run_ref         text,
  superseded_by   bigint REFERENCES knowledge.edge(id),
  legacy_dossier_entry_id bigint UNIQUE REFERENCES knowledge.dossier_entry(id),
  created_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE NULLS NOT DISTINCT (context_node_id, row_hash),
  CHECK (source_url IS NOT NULL OR item_id IS NOT NULL)
);
CREATE INDEX edge_src ON knowledge.edge (src_node_id, rel);
CREATE INDEX edge_dst ON knowledge.edge (dst_node_id, rel);
CREATE INDEX edge_context_date ON knowledge.edge (context_node_id, occurred_on);
CREATE INDEX node_aliases ON knowledge.node USING gin (aliases);
CREATE INDEX node_embedding ON knowledge.node USING hnsw (embedding extensions.halfvec_cosine_ops);

-- What "we know everything" means for a node kind; an empty item is a gap for the completion queue.
CREATE TABLE knowledge.checklist_item (
  node_kind    text NOT NULL,
  item         text NOT NULL,
  rels         text[] NOT NULL,
  min_count    integer NOT NULL DEFAULT 1,
  needs_amount boolean NOT NULL DEFAULT false,
  PRIMARY KEY (node_kind, item)
);
INSERT INTO knowledge.checklist_item (node_kind, item, rels, min_count, needs_amount) VALUES
  ('programme', 'history',               '{milestone}',             3, false),
  ('programme', 'buyers_and_funding',    '{orders,funds}',          1, false),
  ('programme', 'prices_and_amounts',    '{milestone,price,orders,funds}', 1, true),
  ('programme', 'suppliers',             '{supplies,depends_on}',   1, false),
  ('programme', 'export_and_restrictions','{restricts,exports_to}', 1, false),
  ('programme', 'regulation',            '{regulates}',             1, false),
  ('programme', 'technology',            '{part_of}',               1, false),
  ('company',   'products',              '{produces}',              1, false),
  ('company',   'programmes',            '{supplies,part_of}',      1, false),
  ('company',   'customers',             '{orders}',                1, false),
  ('company',   'suppliers',             '{supplies,depends_on}',   1, false);

CREATE VIEW knowledge.node_gap WITH (security_invoker = true) AS
SELECT n.id AS node_id, n.kind, n.name, c.item,
       count(e.id) AS have, c.min_count AS need,
       (SELECT count(*) FROM knowledge.edge r
         WHERE n.id IN (r.src_node_id, r.dst_node_id, r.context_node_id)
           AND r.created_at > now() - interval '14 days') AS recent_mentions
  FROM knowledge.node n
  JOIN knowledge.checklist_item c ON c.node_kind = n.kind
  LEFT JOIN knowledge.edge e ON n.id IN (e.src_node_id, e.dst_node_id, e.context_node_id)
        AND e.rel = ANY(c.rels) AND e.superseded_by IS NULL AND (NOT c.needs_amount OR e.amount IS NOT NULL)
 WHERE n.status <> 'suspect'
 GROUP BY n.id, n.kind, n.name, c.item, c.min_count
HAVING count(e.id) < c.min_count;

ALTER TABLE knowledge.node           ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.node_document  ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.edge           ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.checklist_item ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON knowledge.node, knowledge.node_document, knowledge.edge, knowledge.checklist_item, knowledge.node_gap
  FROM PUBLIC, anon, authenticated;
