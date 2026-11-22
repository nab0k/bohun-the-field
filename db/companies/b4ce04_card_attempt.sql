-- B4CE-04 step 3.5 (EXP-033): one row per autonomous card attempt, written or not, with its reason.
-- Lets the nightly run skip companies tried recently and lets the owner see what was held and why.
create table if not exists companies.card_attempt (
  id bigint generated always as identity primary key,
  company_id text not null references companies.company(id),
  run text not null,
  outcome text not null check (outcome in ('written', 'fail', 'held_l0', 'held_activity', 'error', 'skipped')),
  level text,
  reason text,
  attempted_at timestamptz not null default now()
);
create index if not exists card_attempt_company_idx on companies.card_attempt (company_id, attempted_at desc);
alter table companies.card_attempt enable row level security;

-- Site export of auto-written cards (approved claims only). Callable by the service role only: the site job
-- (bohundefense company-cards-from-db.yml) builds profiles from it and opens a pull request.
create or replace function public.export_company_cards_auto()
returns jsonb language sql stable security definer set search_path = '' as $$
  with auto as (
    select p.* from companies.profile_claim p
    where p.qa_status = 'APPROVED' and p.review_note like 'B4CE-04 auto%')
  select coalesce(jsonb_agg(x order by x->>'company_id'), '[]'::jsonb) from (
    select jsonb_build_object(
      'company_id', c.id, 'name', c.name, 'country', c.country,
      'reviewed', (select min(a.created_at)::date::text from auto a where a.company_id = c.id),
      'claims', (select jsonb_agg(jsonb_build_object('ref', a.claim_ref, 'path', a.field_path, 'value', a.claim_value) order by a.id)
                 from auto a where a.company_id = c.id),
      'sources', (select jsonb_agg(jsonb_build_object('ref', s.source_ref, 'title', s.title, 'url', s.source_url, 'kind', s.source_kind) order by s.id)
                  from companies.profile_source s where s.company_id = c.id and exists (
                    select 1 from companies.profile_claim_source l join auto a on a.id = l.claim_id where l.source_id = s.id))) as x
    from companies.company c where exists (select 1 from auto a where a.company_id = c.id)) t;
$$;
revoke all on function public.export_company_cards_auto() from public, anon, authenticated;
grant execute on function public.export_company_cards_auto() to service_role;
