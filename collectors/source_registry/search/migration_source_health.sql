-- A6SH-02 (applied 20.11 as migration a6sh02_source_health). Shadow schema only; RLS on, no anon access.
create table if not exists source_discovery.source_health (
  channel_id text primary key, updated_at timestamptz not null default now(),
  status text not null check (status in ('healthy','silent','failing','useless','new')),
  polls_7d integer not null default 0, errors_streak integer not null default 0, last_new_at timestamptz,
  items_30d integer not null default 0, in_events_30d integer not null default 0, usefulness numeric, action text);
alter table source_discovery.source_health enable row level security;
revoke all on source_discovery.source_health from anon, authenticated;
