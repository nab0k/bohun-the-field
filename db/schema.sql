-- BOHUN / THE FIELD — единая схема данных v0.1 (09.10.2026)
-- Цель: Cloudflare D1 (SQLite). Совместима с PostgreSQL при замене
-- TEXT-дат на timestamptz и TEXT-JSON на jsonb (см. docs/DATA_MODEL.md).
--
-- Принципы:
--  1. Сырые данные (raw_item) неизменяемы и отделены от обработанных.
--  2. Каждая публикуемая запись проходит гейты:
--     candidate → verified → qa_passed → published (или rejected / unresolved).
--  3. Каждый факт, извлечённый кодом или AI, ссылается на источник (fact).
--  4. Карта, поиск, вид страны и Newsfeed читают одну таблицу entity.
--  5. Украина: без адресов, координат предприятий и людей (CHECK ниже + код).

PRAGMA foreign_keys = ON;

-- ───────────────────────── Справочники ─────────────────────────

CREATE TABLE country (
  iso2            TEXT PRIMARY KEY,               -- 'UA', 'PL', 'US'
  name_en         TEXT NOT NULL,
  name_uk         TEXT,
  region          TEXT,                           -- 'Europe', 'North America'…
  is_eu           INTEGER NOT NULL DEFAULT 0,
  is_nato         INTEGER NOT NULL DEFAULT 0,
  eligibility     TEXT NOT NULL DEFAULT 'review'
                  CHECK (eligibility IN ('eligible','review','excluded')),
  eligibility_basis TEXT,                         -- основание решения
  eligibility_date  TEXT                          -- дата решения
);

-- Дерево категорий продукции и уровней supply chain
CREATE TABLE category (
  id              TEXT PRIMARY KEY,               -- 'uas', 'uas.propulsion'
  parent_id       TEXT REFERENCES category(id),
  name_en         TEXT NOT NULL,
  name_uk         TEXT,
  chain_level     TEXT CHECK (chain_level IN
                  ('raw_material','material','component','subsystem',
                   'platform','integrator','service_testing') OR chain_level IS NULL),
  sort_order      INTEGER NOT NULL DEFAULT 0
);

-- Источники данных: ленты, API, реестры, ручной ввод
CREATE TABLE source (
  id              TEXT PRIMARY KEY,               -- 'ted', 'inoreader:BOHUN_CAPITAL', 'manual'
  kind            TEXT NOT NULL CHECK (kind IN
                  ('rss','inoreader','api','registry','website','manual','llm_research')),
  name            TEXT NOT NULL,
  url             TEXT,
  license         TEXT,                           -- 'CC BY 4.0', 'EU open data', 'headline+link only'
  terms_url       TEXT,
  commercial_use  TEXT NOT NULL DEFAULT 'unknown'
                  CHECK (commercial_use IN ('allowed','attribution','forbidden','unknown')),
  fetch_schedule  TEXT,                           -- cron-выражение или 'manual'
  is_active       INTEGER NOT NULL DEFAULT 1,
  last_fetch_at   TEXT,
  last_fetch_ok   INTEGER,
  last_error      TEXT
);

-- ───────────────────────── Сырые данные ─────────────────────────

-- То, что пришло от источника, как есть. Никогда не редактируется.
CREATE TABLE raw_item (
  id              INTEGER PRIMARY KEY,
  source_id       TEXT NOT NULL REFERENCES source(id),
  external_id     TEXT NOT NULL,                  -- notice ID TED, id статьи Inoreader, URL
  fetched_at      TEXT NOT NULL,
  content_hash    TEXT NOT NULL,                  -- sha256 нормализованного payload
  payload         TEXT NOT NULL,                  -- JSON / XML как текст
  processed_at    TEXT,                           -- когда превращён в entity / news_item
  process_error   TEXT,
  UNIQUE (source_id, external_id, content_hash)   -- идемпотентный импорт; новая версия = новый hash
);
CREATE INDEX raw_item_unprocessed ON raw_item(processed_at) WHERE processed_at IS NULL;

-- ───────────────────────── Сущности ─────────────────────────

-- Общая таблица всего, что стоит на карте и в поиске.
CREATE TABLE entity (
  id              TEXT PRIMARY KEY,               -- 'org_…', 'evt_…', 'opp_…' (ULID)
  kind            TEXT NOT NULL CHECK (kind IN
                  ('organization','event','funding_round','opportunity',
                   'program','policy')),
  slug            TEXT NOT NULL UNIQUE,           -- для URL страницы
  name            TEXT NOT NULL,
  summary_en      TEXT,                           -- собственный текст, не копия источника
  summary_uk      TEXT,
  country_iso2    TEXT REFERENCES country(iso2),
  city            TEXT,
  -- Координаты: точка на карте. Для Украины всегда NULL (центроид страны в клиенте).
  lat             REAL,
  lon             REAL,
  website         TEXT,
  primary_source_url TEXT,                        -- главный первоисточник записи
  -- Гейты публикации
  status          TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN
                  ('candidate','verified','qa_passed','published','rejected','unresolved')),
  status_reason   TEXT,
  checked_at      TEXT,                           -- дата последней проверки человеком
  checked_by      TEXT,
  confidence      TEXT CHECK (confidence IN ('high','medium','low') OR confidence IS NULL),
  flags           TEXT NOT NULL DEFAULT '[]',     -- JSON: ["china_controlled","sanctions_review"]
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  CHECK (NOT (country_iso2 = 'UA' AND (lat IS NOT NULL OR lon IS NOT NULL)))
);
CREATE INDEX entity_kind_status ON entity(kind, status);
CREATE INDEX entity_by_country  ON entity(country_iso2, kind);

CREATE TABLE entity_alias (
  entity_id       TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  alias           TEXT NOT NULL,
  PRIMARY KEY (entity_id, alias)
);

CREATE TABLE entity_category (
  entity_id       TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  category_id     TEXT NOT NULL REFERENCES category(id),
  PRIMARY KEY (entity_id, category_id)
);

-- Дополнительные страны (событие в нескольких странах, программа ЕС и т. п.)
CREATE TABLE entity_country (
  entity_id       TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  country_iso2    TEXT NOT NULL REFERENCES country(iso2),
  PRIMARY KEY (entity_id, country_iso2)
);

-- Связи между сущностями: дочерняя компания, инвестор раунда, организатор события…
CREATE TABLE entity_relation (
  from_id         TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  to_id           TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  relation        TEXT NOT NULL CHECK (relation IN
                  ('parent_of','invested_in','lead_investor_of','organizer_of',
                   'buyer_of','member_of','supplies','may_supply','partner_of')),
  source_url      TEXT,
  PRIMARY KEY (from_id, to_id, relation)
);

-- ───────────────────────── Детали по типам ─────────────────────────

CREATE TABLE organization (
  entity_id       TEXT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  org_type        TEXT NOT NULL CHECK (org_type IN
                  ('company','association','cluster','accelerator','fund',
                   'government_agency','international_body','research')),
  focus           TEXT CHECK (focus IN ('military','dual_use','civil') OR focus IS NULL),
  legal_id        TEXT,                           -- публичный рег. номер, LEI
  hq_address      TEXT,                           -- официальный адрес штаб-квартиры; для UA NULL
  products        TEXT,                           -- краткое описание продуктов своими словами
  fund_size_eur   REAL,                           -- для фондов
  employees_band  TEXT
);

-- Публичные руководители (только с сайта компании / официальных реестров).
-- Без личных контактов. Для компаний Украины не заполняется.
CREATE TABLE public_person (
  id              INTEGER PRIMARY KEY,
  org_entity_id   TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  full_name       TEXT NOT NULL,
  role            TEXT NOT NULL,
  source_url      TEXT NOT NULL,
  checked_at      TEXT NOT NULL
);

CREATE TABLE event (
  entity_id       TEXT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  event_type      TEXT CHECK (event_type IN
                  ('exhibition','conference','forum','demo_day','hackathon','other')),
  starts_on       TEXT,
  ends_on         TEXT,
  venue           TEXT,
  recurrence      TEXT,                           -- 'biennial', 'annual'
  registration_url TEXT
);
CREATE INDEX event_dates ON event(starts_on);

CREATE TABLE funding_round (
  entity_id       TEXT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  company_entity_id TEXT REFERENCES entity(id),
  round_type      TEXT,                           -- 'seed','series_a','grant','m&a'…
  announced_on    TEXT,
  amount          REAL,                           -- только если есть в источнике (см. fact)
  currency        TEXT,
  amount_eur      REAL                            -- пересчёт с курсом на дату
);

-- Тендеры, конкурсы грантов, челленджи (TED, NSPA, EDF, EIC, DIANA, SAM.gov…)
CREATE TABLE opportunity (
  entity_id       TEXT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  opp_type        TEXT NOT NULL CHECK (opp_type IN
                  ('tender','grant_call','challenge','accelerator_call','prior_information')),
  source_notice_id TEXT,                          -- '123456-2026' для TED
  notice_type     TEXT,                           -- тип объявления источника (cn-standard, can…)
  buyer_entity_id TEXT REFERENCES entity(id),
  buyer_name      TEXT,
  cpv_main        TEXT,
  cpv_additional  TEXT,                           -- JSON-массив
  published_on    TEXT,
  deadline_at     TEXT,                           -- NULL = неизвестен, не «открыт»
  value_amount    REAL,
  value_currency  TEXT,
  lifecycle       TEXT NOT NULL DEFAULT 'unknown' CHECK (lifecycle IN
                  ('unknown','open','closing_soon','closed','awarded','cancelled')),
  lifecycle_checked_at TEXT
);
CREATE INDEX opportunity_deadline ON opportunity(lifecycle, deadline_at);

-- Государственные программы и инструменты (EDF, NATO Innovation Fund, Brave1…)
CREATE TABLE program (
  entity_id       TEXT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  operator_entity_id TEXT REFERENCES entity(id),
  budget_amount   REAL,
  budget_currency TEXT,
  period_start    TEXT,
  period_end      TEXT
);

-- Регулирование и страновые показатели: одно утверждение = одна запись
CREATE TABLE policy (
  entity_id       TEXT PRIMARY KEY REFERENCES entity(id) ON DELETE CASCADE,
  topic           TEXT NOT NULL CHECK (topic IN
                  ('defence_budget','export_control','procurement','fdi_screening',
                   'security_agreement_ua','joint_production','other')),
  metric_value    REAL,                           -- напр. % ВВП
  metric_unit     TEXT,
  as_of           TEXT NOT NULL,
  legal_ref       TEXT                            -- '2021/821', 'ITAR 22 CFR 120'
);

-- ───────────────────────── Newsfeed ─────────────────────────

-- Только заголовок, источник, дата, ссылка. Полный текст не храним.
CREATE TABLE news_item (
  id              INTEGER PRIMARY KEY,
  source_id       TEXT NOT NULL REFERENCES source(id),
  raw_item_id     INTEGER REFERENCES raw_item(id),
  url             TEXT NOT NULL UNIQUE,           -- канонический URL (без utm)
  title           TEXT NOT NULL,
  publisher       TEXT,
  published_at    TEXT NOT NULL,
  lang            TEXT,
  layers          TEXT NOT NULL DEFAULT '[]',     -- JSON: ["industry","capital","ukraine"]
  country_iso2    TEXT REFERENCES country(iso2),
  cluster_id      INTEGER,                        -- одна история из разных источников
  tagged_by       TEXT NOT NULL DEFAULT 'rules' CHECK (tagged_by IN ('rules','llm','human')),
  is_hidden       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX news_recent  ON news_item(published_at DESC) WHERE is_hidden = 0;
CREATE INDEX news_country ON news_item(country_iso2, published_at DESC);

CREATE TABLE news_entity (
  news_id         INTEGER NOT NULL REFERENCES news_item(id) ON DELETE CASCADE,
  entity_id       TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  PRIMARY KEY (news_id, entity_id)
);

-- ───────────────────────── Происхождение фактов ─────────────────────────

-- Каждое значение, которое извлёк код или модель (сумма, дедлайн, компания),
-- хранится с цитатой из первоисточника. Нет цитаты — нет факта на сайте.
CREATE TABLE fact (
  id              INTEGER PRIMARY KEY,
  entity_id       TEXT NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
  field           TEXT NOT NULL,                  -- 'opportunity.deadline_at', 'funding_round.amount'
  value           TEXT NOT NULL,
  source_url      TEXT NOT NULL,
  raw_item_id     INTEGER REFERENCES raw_item(id),
  evidence        TEXT,                           -- короткая цитата (≤ 200 символов), проверяемая кодом
  extracted_by    TEXT NOT NULL CHECK (extracted_by IN ('rule','llm','human')),
  model           TEXT,                           -- имя модели, если llm
  evidence_verified INTEGER NOT NULL DEFAULT 0,   -- код нашёл evidence в тексте источника
  extracted_at    TEXT NOT NULL,
  superseded_by   INTEGER REFERENCES fact(id)
);
CREATE INDEX fact_entity ON fact(entity_id, field);

-- ───────────────────────── Служебное ─────────────────────────

-- История изменений: кто, что, когда
CREATE TABLE audit_log (
  id              INTEGER PRIMARY KEY,
  at              TEXT NOT NULL,
  actor           TEXT NOT NULL,                  -- 'collector:ted', 'human:serhii', 'llm:enrich'
  entity_id       TEXT,
  action          TEXT NOT NULL,                  -- 'create','update','status','merge'
  diff            TEXT                            -- JSON
);
CREATE INDEX audit_entity ON audit_log(entity_id, at);

-- Запуски сборщиков: для мониторинга и уведомлений в Telegram
CREATE TABLE job_run (
  id              INTEGER PRIMARY KEY,
  job             TEXT NOT NULL,                  -- 'collect:ted', 'collect:inoreader', 'build:site'
  started_at      TEXT NOT NULL,
  finished_at     TEXT,
  ok              INTEGER,
  items_in        INTEGER,
  items_new       INTEGER,
  error           TEXT
);

-- Кэш AI-обработки: один и тот же текст не отправляется в модель дважды
CREATE TABLE llm_cache (
  input_hash      TEXT NOT NULL,
  task            TEXT NOT NULL,                  -- 'classify_news', 'extract_tender'
  model           TEXT NOT NULL,
  output          TEXT NOT NULL,                  -- JSON
  created_at      TEXT NOT NULL,
  PRIMARY KEY (input_hash, task, model)
);

-- Публичное представление: только прошедшее гейты
CREATE VIEW public_entity AS
  SELECT * FROM entity WHERE status = 'published';
