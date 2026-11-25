"""Catalogue refresh by parts (owner 23.11.2026 «Перепроверка каталога»; design EXP-033
DESIGN_COMPANY_DISCOVERY_v0.md, section 8).

Not every company at once, but all the time (a worker every 20 minutes, scripts/companies-worker.py). A company is due when:
1. an event names it after its last check (a new mention in the news or a synthesis) — first in line;
2. its period has passed; the period depends on what is known:
   card L1–L2 — 30 days, card L3–L4 — 60 days, candidate with a register route or an LEI — 90 days,
   other candidates — 180 days;
3. it was never checked.
Each pass takes the events first, then the largest companies (mentions + contracts and grants), then the most
overdue (overdue time / period). Carded companies are
re-run through the autofill in refresh mode (a card is replaced only when the new one passes the gate and adds
programmes or a new level, never when it would lose data); candidates with a route go through the ordinary
autofill; companies without a route are left to the route step (LEI / Wikidata).
"""
from datetime import date

PERIODS = {'L1': 30, 'L2': 30, 'L3': 60, 'L4': 60, 'card': 60, 'route': 90, 'other': 180}

DUE_SQL = """with c as (
  select c.id, c.country,
    (select p.claim_value->>'level' from companies.profile_claim p where p.company_id = c.id
       and p.claim_ref = 'b4:level' and p.qa_status = 'APPROVED' order by p.id desc limit 1) as level,
    exists (select 1 from companies.profile_claim p where p.company_id = c.id and p.qa_status = 'APPROVED') as has_card,
    (coalesce(c.country in ('CZ','NO','FR','FI','PL'), false)
      or exists (select 1 from companies.company_identifier i where i.company_id = c.id and i.scheme = 'LEI')) as has_route,
    (select max(a.attempted_at) from companies.card_attempt a where a.company_id = c.id) as last_card,
    (select max(r.attempted_at) from companies.route_attempt r where r.company_id = c.id) as last_route,
    (select max(l.created_at) from companies.mention_link l where l.company_id = c.id) as last_event,
    -- size: how much the world writes about it and how much it wins (owner 23.11.2026: large companies first)
    (select count(*) from companies.mention_link l where l.company_id = c.id)
      + (select count(*) from companies.signal s where s.company_id = c.id) as size
  from companies.company c where c.excluded_reason is null),
k as (select *,
  -- a company with a card or a register route is checked by a card attempt; the route step only finds the route
  -- (fix 25.11.2026: a route attempt made 797 never-filled companies look checked)
  case when has_card or has_route then last_card else greatest(last_card, last_route) end as last_check from c)
select id, country, level, has_card, has_route, last_check::date::text as last_check, size,
  (last_event is not null and (last_check is null or last_event > last_check)) as event
from k"""
METRIC_SQL = """insert into companies.refresh_metric (day, total, on_time, overdue, never_checked, checked_today, by_tier)
values (%(day)s, %(total)s, %(on_time)s, %(overdue)s, %(never)s, %(today)s, %(tiers)s::jsonb)
on conflict (day) do update set total = excluded.total, on_time = excluded.on_time, overdue = excluded.overdue,
  never_checked = excluded.never_checked, checked_today = excluded.checked_today, by_tier = excluded.by_tier,
  created_at = now()"""


def tier(r):
    if r['has_card']:
        return r['level'] if r['level'] in ('L1', 'L2', 'L3', 'L4') else 'card'
    return 'route' if r['has_route'] else 'other'


def due(rows, today=None):
    """[(row, tier, priority)] of the due companies in the order they are taken (owner 23.11.2026: work all the time;
    whatever the news, signals and syntheses name goes first, then the large companies, then the rest by schedule):
    1. named after the last check, larger first; 2. never checked or overdue, larger first, then more overdue."""
    today = today or date.today()
    out = []
    for r in rows:
        t, size = tier(r), r.get('size') or 0
        if r['event']:
            out.append((r, t, (2, size, 0)))
            continue
        if not r['last_check']:
            out.append((r, t, (1, size, 1.0)))
            continue
        age = (today - date.fromisoformat(r['last_check'])).days
        if age >= PERIODS[t]:
            out.append((r, t, (1, size, age / PERIODS[t])))
    return sorted(out, key=lambda x: tuple(-v for v in x[2]))


def metric(rows, today=None):
    """Share of the catalogue checked within its period, by tier."""
    today = today or date.today()
    tiers, on_time, overdue, never, now = {}, 0, 0, 0, 0
    for r in rows:
        t = tier(r)
        s = tiers.setdefault(t, {'total': 0, 'on_time': 0})
        s['total'] += 1
        if not r['last_check']:
            never += 1
            continue
        age = (today - date.fromisoformat(r['last_check'])).days
        now += age == 0
        if age < PERIODS[t]:
            on_time += 1
            s['on_time'] += 1
        else:
            overdue += 1
    return {'day': today.isoformat(), 'total': len(rows), 'on_time': on_time, 'overdue': overdue, 'never': never,
            'today': now, 'tiers': tiers}


def plan(rows, budget, today=None):
    """(refresh ids: carded, fill ids: candidates with a route) within the budget; the rest wait for the route step."""
    refresh, fill = [], []
    for r, t, _ in due(rows, today):
        if len(refresh) + len(fill) >= budget:
            break
        if r['has_card'] and r['has_route']:
            refresh.append(r['id'])  # a card without a register route is refreshed by its own step (contracts)
        elif r['has_route']:
            fill.append(r['id'])
    return refresh, fill
