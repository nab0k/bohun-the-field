-- A1SD-02: shadow source registry in the cloud (D-20261119-A1SD-CLOUD-STORE-A).
-- Mirrors the local SQLite tables of admission.py so a daily GitHub run can load state, run one cycle and write
-- back. Nothing here is read by the product (intelligence.sources is untouched). Same access pattern as the other
-- private schemas: no anon/authenticated access, RLS on, the runner connects as the database owner.
create schema if not exists source_discovery;
revoke all on schema source_discovery from anon, authenticated;

create table source_discovery.source_state (
  channel_id     text primary key,
  canonical_url  text not null unique,
  status         text not null check (status in ('accepted','rejected','candidate','no_news','on_hold','general_outlet')),
  type           text,
  delivery       text,
  country        text,
  language       text,
  since          timestamptz not null,
  rules_version  text not null
);

create table source_discovery.candidate_seen (
  candidate_url  text primary key,
  last_decision  text not null,
  decided_at     timestamptz not null,
  transient      integer not null default 0,
  channel_id     text
);

create table source_discovery.admission_decision (
  decision_id    bigint generated always as identity primary key,
  candidate_url  text not null,
  channel_id     text,
  decision       text not null check (decision in ('accept','reject','disputed','hold','general','retry')),
  reasons        jsonb not null,
  record         jsonb not null,
  rules_version  text not null,
  decided_at     timestamptz not null,
  found_via      jsonb,
  run_id         text
);
create index admission_decision_candidate_idx on source_discovery.admission_decision (candidate_url, decision_id desc);
create index admission_decision_run_idx on source_discovery.admission_decision (run_id);

create table source_discovery.cycle_run (
  run_id       text primary key,
  started_at   timestamptz not null,
  finished_at  timestamptz,
  where_run    text not null,          -- 'mac' (shadow cycles 1-5) or 'github'
  plan         jsonb,
  trace        jsonb,
  measure      jsonb,
  done_jobs    jsonb not null default '[]'::jsonb   -- job keys asked in this run (the planner skips them later)
);

alter table source_discovery.source_state enable row level security;
alter table source_discovery.candidate_seen enable row level security;
alter table source_discovery.admission_decision enable row level security;
alter table source_discovery.cycle_run enable row level security;
revoke all on all tables in schema source_discovery from anon, authenticated;

comment on schema source_discovery is
  'A1SD-02 shadow source registry (EXP-034). Not read by the product; accepted sources reach collection only by a separate decision.';
