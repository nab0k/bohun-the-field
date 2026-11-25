-- EXP-035 (owner 25.11.2026 «Yes, build it in»): one row per website search for a company, found or not, with the
-- proof read on the site. Lets the worker skip companies tried in the last 30 days and keep the daily search budget.
create table if not exists companies.website_attempt (
  id bigint generated always as identity primary key,
  company_id text not null references companies.company(id),
  outcome text not null check (outcome in ('found', 'not_found', 'error')),
  website text,
  via text check (via in ('register', 'wikidata', 'search')),
  proof text,
  searched boolean not null default false,
  reason text,
  attempted_at timestamptz not null default now()
);
create index if not exists website_attempt_company_idx on companies.website_attempt (company_id, attempted_at desc);
alter table companies.website_attempt enable row level security;
