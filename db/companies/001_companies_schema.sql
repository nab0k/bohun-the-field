-- EXP-012 — схема `companies` для автономного сбора компаний (Supabase / PostgreSQL).
-- Дизайн: BOHUN-local/docs/experiments/EXP-012-companies-coverage-audit/DESIGN_v0.md (G2 18.10.2026).
-- Применяется один раз командой `companies-collect.py --store supabase --migrate` (T08, согласие 18.10.2026);
-- до запуска workflow в Supabase не применена. Production-перенос — отдельное согласие.
--
-- Принципы:
--  1. observation неизменяема: сырой ответ источника хранится один раз на (source, source_key, content_hash).
--  2. Компании не сливаются автоматически по похожему имени: неочевидное совпадение уходит в match_candidate.
--  3. Слой A–D вычисляется из signal и прослеживается до observation.
--  4. РФ/КНР: юр. страна или конечный родитель → status = 'excluded' (запись сохраняется).
--     Частичное владение РФ/КНР → не исключение, а запись в company_ownership_link и флаг.
--  5. Украина: без адресов и координат предприятий (CHECK ниже + код).
--  6. Публичного доступа нет: anon/authenticated не получают прав.

CREATE SCHEMA IF NOT EXISTS companies;

CREATE TABLE companies.company (
  id               text PRIMARY KEY,                       -- 'co_' + ULID-подобный ключ
  name             text NOT NULL,
  name_norm        text NOT NULL,
  country          char(2),                                -- юридическая страна ISO 3166-1 alpha-2, NULL = не установлена
  city             text,                                   -- тип адреса — в company_alias/observation, не HQ по умолчанию
  website          text,
  supply_role      text CHECK (supply_role IN ('PRIME','TIER1','TIER2_3','MATERIALS','SERVICES','RND')),
  defence_relation text CHECK (defence_relation IN ('DEDICATED','DUAL_USE','CIVIL_SUPPLIER')),
  flags            jsonb NOT NULL DEFAULT '[]'::jsonb,
  layer            char(1) CHECK (layer IN ('A','B','C','D')),
  status           text NOT NULL DEFAULT 'candidate'
                   CHECK (status IN ('candidate','card','dossier','excluded')),
  excluded_reason  text,
  ownership_check  text,                                   -- например 'OPEN:GLEIF_NO_LEI'
  notion_page_id   text UNIQUE,                            -- наследие DB | Companies (all countries)
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CHECK (NOT (country = 'UA' AND city IS NOT NULL)),
  CHECK ((status = 'excluded') = (excluded_reason IS NOT NULL))
);
CREATE INDEX company_name_country ON companies.company (name_norm, country);
CREATE INDEX company_status_layer ON companies.company (status, layer);

CREATE TABLE companies.company_identifier (
  scheme       text NOT NULL,                              -- UEI, LEI, EDRPOU, TED_ORG, CAGE, NATIONAL:<реестр>
  value        text NOT NULL,
  company_id   text NOT NULL REFERENCES companies.company(id) ON DELETE CASCADE,
  source       text NOT NULL,
  observed_at  timestamptz NOT NULL,
  PRIMARY KEY (scheme, value)
);
CREATE INDEX company_identifier_company ON companies.company_identifier (company_id);

CREATE TABLE companies.company_alias (
  company_id   text NOT NULL REFERENCES companies.company(id) ON DELETE CASCADE,
  alias        text NOT NULL,
  alias_norm   text NOT NULL,
  source       text NOT NULL,
  PRIMARY KEY (company_id, alias_norm)
);
CREATE INDEX company_alias_norm ON companies.company_alias (alias_norm);

CREATE TABLE companies.observation (
  id            bigserial PRIMARY KEY,
  source        text NOT NULL,
  source_key    text NOT NULL,
  content_hash  text NOT NULL,                             -- sha256 канонического JSON
  fetched_at    timestamptz NOT NULL,
  payload       jsonb NOT NULL,
  UNIQUE (source, source_key, content_hash)
);

CREATE TABLE companies.signal (
  id              bigserial PRIMARY KEY,
  company_id      text NOT NULL REFERENCES companies.company(id) ON DELETE CASCADE,
  type            text NOT NULL CHECK (type IN
                  ('contract','subcontract','grant','tender_award','export_licence','ncage',
                   'registry_code','self_declared','exhibition','association','fund_portfolio',
                   'news','legacy_assessment')),
  layer           char(1) NOT NULL CHECK (layer IN ('A','B','C','D')),
  counterpart     text,                                    -- заказчик, прайм или программа
  signal_date     date,
  url             text,
  observation_id  bigint REFERENCES companies.observation(id),
  UNIQUE (company_id, type, observation_id)
);

CREATE TABLE companies.company_category (
  company_id  text NOT NULL REFERENCES companies.company(id) ON DELETE CASCADE,
  axis        text NOT NULL CHECK (axis IN ('domain','capability')),
  value       text NOT NULL,
  basis       text NOT NULL,                               -- 'CPV:35613000', 'NAICS:336414', 'PSC:1420', 'LEGACY:drones'
  PRIMARY KEY (company_id, axis, value)
);

CREATE TABLE companies.company_relation (
  from_id   text NOT NULL REFERENCES companies.company(id) ON DELETE CASCADE,
  to_id     text NOT NULL REFERENCES companies.company(id) ON DELETE CASCADE,
  relation  text NOT NULL CHECK (relation IN ('parent_of','supplies','member_of')),
  source    text NOT NULL,
  PRIMARY KEY (from_id, to_id, relation)
);

CREATE TABLE companies.company_ownership_link (
  company_id     text NOT NULL REFERENCES companies.company(id) ON DELETE CASCADE,
  owner_country  char(2) NOT NULL,
  owner_name     text,
  share_text     text,                                     -- '25-50%', '12.5%', NULL = не раскрыто
  link_type      text NOT NULL CHECK (link_type IN ('direct','indirect','minority','ultimate_parent')),
  source         text NOT NULL,
  observed_at    timestamptz NOT NULL,
  PRIMARY KEY (company_id, owner_country, link_type, source)
);

CREATE TABLE companies.match_candidate (
  observation_id  bigint NOT NULL REFERENCES companies.observation(id),
  company_id      text NOT NULL REFERENCES companies.company(id) ON DELETE CASCADE,
  reason          text NOT NULL,
  mention_name    text NOT NULL,
  PRIMARY KEY (observation_id, company_id)
);

CREATE TABLE companies.source_state (
  source               text PRIMARY KEY,
  cursor               text,
  consecutive_failures integer NOT NULL DEFAULT 0,
  disabled             boolean NOT NULL DEFAULT false,
  updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE companies.discovery_candidate (
  id bigint GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  observation_id bigint NOT NULL REFERENCES companies.observation(id),
  name text NOT NULL, name_norm text NOT NULL, reported_country text,
  website text, role text NOT NULL DEFAULT 'lead',
  status text NOT NULL DEFAULT 'discovered'
    CHECK (status IN ('discovered','candidate_existing','hold','rejected','promotion_ready')),
  matched_company_id text REFERENCES companies.company(id),
  match_reason text, created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(observation_id,name_norm)
);
CREATE INDEX dc_status ON companies.discovery_candidate(status);
CREATE TABLE companies.schema_migration (
  version integer PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE companies.run (
  id               bigserial PRIMARY KEY,
  source           text NOT NULL,
  started_at       timestamptz NOT NULL,
  finished_at      timestamptz,
  cursor_in        text,
  cursor_out       text,
  fetched          integer NOT NULL DEFAULT 0,
  new_observations integer NOT NULL DEFAULT 0,
  new_companies    integer NOT NULL DEFAULT 0,
  matched          integer NOT NULL DEFAULT 0,
  candidates       integer NOT NULL DEFAULT 0,
  skipped          integer NOT NULL DEFAULT 0,
  discoveries      integer NOT NULL DEFAULT 0,
  candidate_existing integer NOT NULL DEFAULT 0,
  holds            integer NOT NULL DEFAULT 0,
  status           text NOT NULL CHECK (status IN ('ok','error','skipped_disabled')),
  error            text
);

-- RLS включён без политик: через API (anon/authenticated) строки недоступны; сервисное подключение postgres его обходит.
ALTER TABLE companies.company ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.company_identifier ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.company_alias ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.observation ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.signal ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.company_category ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.company_relation ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.company_ownership_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.match_candidate ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.source_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.run ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.discovery_candidate ENABLE ROW LEVEL SECURITY;
ALTER TABLE companies.schema_migration ENABLE ROW LEVEL SECURITY;

-- Доступ: только сервисная роль (используется GitHub Actions после отдельного согласия).
REVOKE ALL ON SCHEMA companies FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA companies FROM PUBLIC;
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN
    EXECUTE 'REVOKE ALL ON SCHEMA companies FROM anon';
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA companies FROM anon';
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
    EXECUTE 'REVOKE ALL ON SCHEMA companies FROM authenticated';
    EXECUTE 'REVOKE ALL ON ALL TABLES IN SCHEMA companies FROM authenticated';
  END IF;
END $$;
