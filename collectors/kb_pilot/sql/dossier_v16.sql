-- EXP-028 DESIGN §3/§12 — dossier tables v16 (D-20261029-EXP028-DOSSIER-DB).
-- Additive: new closed tables in schema knowledge only; existing objects and their rights unchanged.
CREATE TABLE knowledge.programme (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  key          text NOT NULL UNIQUE CHECK (key ~ '^[a-z0-9][a-z0-9 -]{1,159}$'),  -- normalised name
  name         text NOT NULL,
  aliases      text[] NOT NULL DEFAULT '{}',
  topic_id     bigint REFERENCES knowledge.topic(id),
  summary      text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge.document (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  url          text NOT NULL UNIQUE CHECK (url ~ '^https?://'),
  host         text NOT NULL,
  title        text,
  doc_type     text,
  issuer       text,
  date_text    text,                      -- as found (may be partial)
  official     boolean NOT NULL DEFAULT false,
  storage      text NOT NULL DEFAULT 'link_only' CHECK (storage IN ('link_only','markdown','r2_full','r2_fragments')),
  text_sha256  text CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
  size_bytes   integer,
  r2_key       text,
  anchors      jsonb NOT NULL DEFAULT '[]' CHECK (jsonb_typeof(anchors) = 'array'),
  fetched_at   timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE knowledge.programme_document (
  programme_id bigint NOT NULL REFERENCES knowledge.programme(id) ON DELETE CASCADE,
  document_id  bigint NOT NULL REFERENCES knowledge.document(id) ON DELETE CASCADE,
  PRIMARY KEY (programme_id, document_id)
);
CREATE TABLE knowledge.dossier_entry (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  programme_id   bigint NOT NULL REFERENCES knowledge.programme(id) ON DELETE CASCADE,
  kind           text NOT NULL CHECK (kind IN ('chronology','supply_chain','restriction')),
  entry_date     date,
  date_precision text CHECK (date_precision IN ('day','month','year')),
  what           text NOT NULL,
  commitment     text,
  amount         numeric(20,2),
  currency       text,
  document_id    bigint REFERENCES knowledge.document(id),
  source_url     text,
  anchor         text,
  archive_item_id bigint REFERENCES intelligence.items(id),
  verify_status  text NOT NULL CHECK (verify_status IN ('ok','check')),
  row_data       jsonb NOT NULL,
  row_hash       text NOT NULL CHECK (row_hash ~ '^[0-9a-f]{64}$'),
  pilot_version  text NOT NULL,
  run_ref        text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (programme_id, row_hash),
  CHECK (source_url IS NOT NULL OR archive_item_id IS NOT NULL)
);
CREATE INDEX dossier_entry_programme_date ON knowledge.dossier_entry (programme_id, entry_date);
CREATE INDEX programme_aliases ON knowledge.programme USING gin (aliases);
ALTER TABLE knowledge.programme          ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.document           ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.programme_document ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge.dossier_entry      ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON knowledge.programme, knowledge.document, knowledge.programme_document, knowledge.dossier_entry
  FROM PUBLIC, anon, authenticated;
