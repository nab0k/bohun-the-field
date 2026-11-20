-- A4SC-05 shadow stage (applied 20.11 as migration a4sc05_feed_collector_shadow). Shadow schema only.
create table if not exists source_discovery.feed_poll (
  id bigserial primary key, run_id text not null, polled_at timestamptz not null default now(),
  channel_id text not null, http_status integer, etag text, last_modified text,
  entries integer not null default 0, fresh integer not null default 0, new_items integer not null default 0, error text);
create index if not exists feed_poll_channel on source_discovery.feed_poll (channel_id, polled_at desc);
create table if not exists source_discovery.feed_items_shadow (
  id bigserial primary key, run_id text not null, seen_at timestamptz not null default now(),
  channel_id text not null, external_id text not null, url text, title text, published_at timestamptz,
  text_chars integer not null default 0, capture_status text, already_in_items boolean not null default false,
  unique (channel_id, external_id));
alter table source_discovery.feed_poll enable row level security;
alter table source_discovery.feed_items_shadow enable row level security;
revoke all on source_discovery.feed_poll from anon, authenticated;
revoke all on source_discovery.feed_items_shadow from anon, authenticated;
