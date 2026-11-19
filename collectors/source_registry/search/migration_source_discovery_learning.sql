-- A1SD-03 wave 1 (D-20261119-A1SD-SELF-IMPROVE-G2): what every run learns. Shadow schema only; RLS on, no access
-- for anon/authenticated (same as the other source_discovery tables).
create table if not exists source_discovery.learning (
  id bigserial primary key,
  run_id text not null,
  at timestamptz not null default now(),
  job_key text not null,
  layer text not null,
  engine text,
  country text,
  type_hint text,
  results integer not null default 0,
  new_sites integer not null default 0,
  accepted integer not null default 0,
  disputed integer not null default 0,
  general integer not null default 0,
  held integer not null default 0,
  rejected integer not null default 0
);
create index if not exists learning_at on source_discovery.learning (at);
alter table source_discovery.learning enable row level security;
revoke all on source_discovery.learning from anon, authenticated;
