"""Contracts of US and Canadian companies for their cards (EXP-035, owner 24.11.2026 «Yes, build them»).

A company of the catalogue in the US or Canada gets, at most once in PERIOD_DAYS:
- US: the money the Department of Defense obligated to it in the last two fiscal years, by buyer, the number of
  contracts and the largest contracts (sources/us_contracts.py, USAspending.gov);
- Canada: its federal contracts, each once, by buyer, and the largest (sources/ca_contracts.py, open.canada.ca);
- both: its sub-contractors already collected from USAspending sub-awards (companies.signal, type subcontract).

Claims: b4:spend (summary), b4:ctr1… (contracts), b4:sub1… (sub-contractors), review note "B4CE-04 contracts …".
They are separate from the programmes (b4:prog…), so the autofill refresh neither compares nor removes them.

A company without a card gets one only on the same rule as the autofill (design B v1): an identity and an
independent official record of a sale to a defence buyer. US: DoD contracts in USAspending (the record) and the
LEI, or else the federal UEI, as the registration number; Canada: an LEI and at least one contract with a
defence buyer. Level L3 verified (R03, defence customer on record).
"""
import json
from datetime import date


PERIOD_DAYS = 30
TAGS = ('b4:spend', 'b4:ctr', 'b4:sub')
NOTE = 'B4CE-04 contracts {day}: contract records of public registers (USAspending, Open Government Canada), EXP-035'
RUN = 'B4CE-04 contracts {day}'

TARGETS_SQL = """with c as (
  select c.id, c.name, c.country,
    exists (select 1 from companies.profile_claim p where p.company_id = c.id and p.qa_status = 'APPROVED'
            and p.claim_ref = 'b4:level') as has_card,
    (select p.claim_value #>> '{}' from companies.profile_claim p where p.company_id = c.id and p.qa_status = 'APPROVED'
       and p.claim_ref = 'b4:legal_name' limit 1) as legal_name,
    (select i.value from companies.company_identifier i where i.company_id = c.id and i.scheme = 'LEI' limit 1) as lei,
    exists (select 1 from companies.profile_claim p where p.company_id = c.id and p.qa_status = 'APPROVED'
            and p.claim_ref = 'b4:city') as has_city,
    (select count(*) from companies.mention_link l where l.company_id = c.id)
      + (select count(*) from companies.signal s where s.company_id = c.id and s.layer in ('A', 'B')) as size,
    (select max(a.attempted_at) from companies.card_attempt a where a.company_id = c.id and a.run like 'B4CE-04 contracts%%') as last,
    exists (select 1 from companies.profile_claim p where p.company_id = c.id and p.qa_status = 'APPROVED'
            and p.claim_ref = 'b4:level' and p.review_note like 'B4CE-04 contracts%%') as by_contracts,
    -- contract claims counted before the defence-code filter (owner 25.11.2026): checked again at once
    exists (select 1 from companies.profile_claim p where p.company_id = c.id and p.qa_status = 'APPROVED'
            and p.claim_ref = 'b4:spend' and not (p.claim_value ? 'scope'))
      and not exists (select 1 from companies.card_attempt a where a.company_id = c.id and a.run like 'B4CE-04 contracts%%'
                      and a.outcome <> 'error' and a.reason like '%%' || %(mark)s) as rescope
  from companies.company c where c.country in ('US', 'CA') and c.excluded_reason is null)
select id, name, country, has_card, has_city, legal_name, lei, size, by_contracts from c
where (has_card or size > 0) and (last is null or last < now() - make_interval(days => %(days)s)
  or (rescope and last < now() - interval '20 minutes')
  -- a card without a city cannot reach the site catalogue: checked again soon (25.11.2026)
  or (has_card and not has_city and last < now() - interval '20 minutes'))
order by rescope desc, has_card desc, size desc, id limit %(limit)s"""
SUBS_SQL = """select c.name as supplier, c.country, s.counterpart as prime, s.signal_date::text as date, s.url
from companies.signal s join companies.company c on c.id = s.company_id
where s.type = 'subcontract' and lower(s.counterpart) = any(%(names)s) and c.excluded_reason is null
order by s.signal_date desc nulls last limit 12"""
NOW_SQL = """select claim_ref, claim_value from companies.profile_claim
where company_id = %(co)s and qa_status = 'APPROVED' and claim_ref ~ '^b4:(spend|ctr|sub)'"""
RETIRE_SQL = """update companies.profile_claim set qa_status = 'REJECTED', updated_at = now(),
  review_note = review_note || ' | replaced by the contracts refresh of ' || %(day)s
where company_id = %(co)s and qa_status = 'APPROVED' and claim_ref ~ '^b4:(spend|ctr|sub)'"""
# the whole card made by this step (identity, level, city and contracts) when its contracts turn out not to be defence
RETIRE_ALL_SQL = """update companies.profile_claim set qa_status = 'REJECTED', updated_at = now(),
  review_note = review_note || ' | withdrawn: no defence contracts under the defence codes, ' || %(day)s
where company_id = %(co)s and qa_status = 'APPROVED' and review_note like 'B4CE-04 contracts%%'"""
ATTEMPT_SQL = """insert into companies.card_attempt (company_id, run, outcome, level, reason) values (%s, %s, %s, %s, %s)"""
# card_attempt.outcome is checked against the autofill outcomes (db/companies/b4ce04_card_attempt.sql + refresh): the
# contracts outcomes are stored as one of them, the detail goes to the reason (25.11.2026: CheckViolation on every pass)
DB_OUTCOME = {'card_made': 'written', 'written': 'written', 'refreshed': 'refreshed', 'unchanged': 'unchanged',
              'no_record': 'skipped', 'no_identity': 'skipped', 'withdrawn': 'skipped', 'error': 'error'}
MARK = ' [defence codes]'  # every attempt since the filter: a company is re-checked under it once (owner 25.11.2026)
SCOPE_US = 'DoD contracts for defence goods and defence R&D (PSC groups of us_contracts.DEFENCE_PSC)'
SCOPE_CA = 'federal contracts; defence = a defence buyer and a defence category (ca_contracts.DEFENCE_CATEGORY)'


def fiscal_window(today):
    """The previous US fiscal year and the current one (FY starts 1 October): FY2026–27 on 24.11.2026, labelled
    "to <today>" because the current year is still running."""
    fy = today.year + (1 if today.month >= 10 else 0)
    return f'{fy - 2}-10-01', f'{fy}-09-30'


def us_claims(d):
    """Claims of a USAspending summary (us_contracts.parse)."""
    top = max(d['buyers'], key=lambda b: b['amount']) if d['buyers'] else None
    out = {'b4:spend': {'kind': 'spend_total', 'register': 'USAspending.gov', 'buyer': 'US Department of Defense', 'period': d['period'],
                        'amount': d['total'], 'currency': 'USD', 'count': d['count'], 'url': d['profile'], 'partial': d.get('partial', False),
                        'top_buyer': top, 'buyers': d['buyers'][:8], 'recipient': d['recipient'], 'uei': d['uei'],
                        'scope': SCOPE_US, 'defence_share': d.get('defence_share'), 'all_dod': d.get('all_dod')}}
    for n, a in enumerate(d['awards'][:8], 1):
        out[f'b4:ctr{n}'] = {'kind': 'contract', 'register': 'USAspending.gov', 'ref': a['ref'], 'buyer': a['buyer'], 'title': a['title'],
                             'amount': a['amount'], 'currency': 'USD', 'date': a['date'], 'end': a['end'], 'url': a['url'],
                             'value_note': 'total value of the contract over its life'}
    return out


def ca_claims(d):
    """Claims of a Canada summary (ca_contracts.summarise)."""
    out = {'b4:spend': {'kind': 'contract_count', 'register': 'Open Government Canada', 'count': d['count'], 'defence': d['defence'],
                        'defence_buyer': d.get('defence_buyer'), 'scope': SCOPE_CA,
                        'first': d['first'], 'last': d['last'], 'buyers': [{'name': b['name'], 'n': b['n']} for b in d['buyers']],
                        'vendors': d['vendors'], 'url': 'https://open.canada.ca/data/en/dataset/d8f85d91-7dec-4fd1-8055-483b77225d8b'}}
    for n, c in enumerate(d['contracts'][:8], 1):
        out[f'b4:ctr{n}'] = {'kind': 'contract', 'register': 'Open Government Canada', 'ref': c['ref'], 'buyer': c['buyer'],
                             'title': c['category'] or 'Contract', 'category': c['category'], 'amount': c['amount'], 'currency': 'CAD',
                             'date': c['date'], 'end': c['end'], 'url': c['url']}
    return out


def sub_claims(rows):
    return {f'b4:sub{n}': {'kind': 'subcontract', 'register': 'USAspending.gov', 'supplier': r['supplier'], 'country': r['country'],
                           'prime': r['prime'], 'date': r['date'], 'url': r['url'] or 'https://www.usaspending.gov/search'}
            for n, r in enumerate(rows, 1)}


def gleif_city(lei, opener=None):
    """Headquarters city of an LEI record (GLEIF, no key), or None."""
    import urllib.request
    opener = opener or urllib.request.urlopen
    try:
        req = urllib.request.Request(f'https://api.gleif.org/api/v1/lei-records/{lei}', headers={'User-Agent': 'BOHUN-research/1.0'})
        with opener(req, timeout=60) as r:
            c = json.load(r)['data']['attributes']['entity']['headquartersAddress']['city']
    except Exception:  # noqa: BLE001
        return None
    return c.title() if c and c.isupper() else c


def identity(t, d):
    """(scheme, value, url, title, kind) for a company without a card, or None (no card is made)."""
    if t['lei']:
        return 'LEI', t['lei'], f'https://search.gleif.org/#/record/{t["lei"]}', 'GLEIF LEI record', 'legal_entity_identifier_reference'
    if t['country'] == 'US' and d.get('uei'):
        return 'US_UEI', d['uei'], d['profile'], 'USAspending recipient profile (SAM UEI)', 'contract_register'
    return None


def payload(t, found, today, card_core=None):
    """{'sources', 'claims'} in the autofill format (autofill.WRITE_SQL)."""
    co = t['id']
    sources, claims = [], []
    reg_src = None
    first = next(iter(found.values()))
    src_url = first.get('url')
    sources.append({'company_id': co, 'ref': 'b4:contracts', 'title': first['register'], 'url': src_url, 'kind': 'contract_register'})
    if card_core:
        scheme, value, url, title, kind = card_core['id']
        if url != src_url:
            sources.append({'company_id': co, 'ref': 'b4:reg', 'title': title, 'url': url, 'kind': kind})
            reg_src = 'b4:reg'
        else:
            sources[0]['ref'] = reg_src = 'b4:reg'
        claims += [
            {'company_id': co, 'ref': 'b4:legal_name', 'path': '/identity/legal_name', 'value': card_core['legal_name'],
             'assessment': 'registry_supported', 'srcs': [reg_src]},
            {'company_id': co, 'ref': 'b4:regno', 'path': '/identity/registration_number', 'value': {'scheme': scheme, 'value': value},
             'assessment': 'registry_supported', 'srcs': [reg_src]},
            *([{'company_id': co, 'ref': 'b4:city', 'path': '/identity/city', 'value': card_core['city'],
                 'assessment': 'registry_supported', 'srcs': [reg_src]}] if card_core.get('city') else []),
            {'company_id': co, 'ref': 'b4:level', 'path': '/classification/defence_level', 'value': {
                'level': 'L3', 'confidence': 'verified', 'rule': 'R03', 'explanation': card_core['why'],
                'classifier': 'collectors/companies/contracts.py', 'run': RUN.format(day=today.isoformat())},
             'assessment': 'cross_source', 'srcs': [sources[0]['ref']]}]
    ctr_src = sources[0]['ref']
    for ref, value in found.items():
        claims.append({'company_id': co, 'ref': ref, 'path': '/contracts/-', 'value': value, 'assessment': 'cross_source', 'srcs': [ctr_src]})
    assert all(s['url'] and s['url'].startswith('https://') for s in sources)
    return {'sources': sources, 'claims': claims}


def same(now_rows, found):
    """True when the contract claims in force equal the new ones (nothing to write)."""
    now = {r['claim_ref']: r['claim_value'] for r in now_rows}
    return json.dumps(now, sort_keys=True) == json.dumps(found, sort_keys=True)


def run(conn, limit=12, write=False, today=None, us_fetch=None, ca_fetch=None, log=print, minutes=None):
    """One pass. conn: psycopg connection with dict rows. us_fetch(name, start, end) / ca_fetch(name, aliases)."""
    from . import autofill as A
    from .sources import ca_contracts as C
    from .sources import us_contracts as U
    today = today or date.today()
    us_fetch = us_fetch or U.fetch
    ca_fetch = ca_fetch or (lambda name, aliases: C.summarise(C.fetch(name, aliases)))
    start, end = fiscal_window(today)
    note, run_label = NOTE.format(day=today.isoformat()), RUN.format(day=today.isoformat())
    targets = conn.execute(TARGETS_SQL, {'days': PERIOD_DAYS, 'limit': limit, 'mark': MARK}).fetchall()
    summary = {'targets': len(targets), 'outcomes': {}, 'cards_made': [], 'withdrawn': [], 'write': write}
    import time
    started = time.monotonic()
    for t in targets:
        if minutes and time.monotonic() - started > minutes * 60:
            break
        outcome, reason = 'no_record', ''
        try:
            names = [n for n in (t['legal_name'], t['name']) if n]
            if t['country'] == 'US':
                d = next((r for r in (us_fetch(n, start, end, today=today) for n in dict.fromkeys(names)) if r), None)
                found = us_claims(d) if d and d['total'] > 0 else {}
                defence = bool(found)
                ident_name = d['recipient'] if d else None
                not_defence = bool(d and d.get('non_defence'))
                why_not = f"{d.get('defence_share', 0):.0%} of the DoD money under the defence codes" if not_defence else ''
            else:
                d = ca_fetch(names[0], names[1:])
                found = ca_claims(d) if d else {}
                defence = bool(d and d['defence'])
                ident_name = (d['vendors'][0] if d and d['vendors'] else None)
                # a Canadian card made by this step needs defence contracts; one made elsewhere keeps its contract list
                not_defence = bool(d and not defence and t.get('by_contracts'))
                why_not = 'no contract with a defence buyer in a defence category' if not_defence else ''
            if not_defence:  # owner 25.11.2026: defence codes only — withdraw what this step wrote
                now = conn.execute(NOW_SQL, {'co': t['id']}).fetchall()
                if t.get('by_contracts') or now:
                    if write:
                        with conn.transaction():
                            conn.execute(RETIRE_ALL_SQL if t.get('by_contracts') else RETIRE_SQL, {'co': t['id'], 'day': today.isoformat()})
                    outcome, reason = 'withdrawn' + ('' if write else '_dry'), why_not
                    if t.get('by_contracts'):
                        summary['withdrawn'].append(f"{t['country']} {t['name']}")
                else:
                    outcome, reason = 'no_record', why_not
                found = {}
            # the city lets the site add a company that is not in the catalogue yet (25.11.2026: Lockheed Martin skipped)
            town = (d or {}).get('city') if t['country'] == 'US' else (gleif_city(t['lei']) if t.get('lei') and found else None)
            if found and t['has_card'] and not t.get('has_city') and town and write:
                src_url = (d or {}).get('profile') if t['country'] == 'US' else f'https://search.gleif.org/#/record/{t["lei"]}'
                with conn.transaction():
                    conn.execute(A.WRITE_SQL, {'j': json.dumps({'sources': [{'company_id': t['id'], 'ref': 'b4:city_src', 'title': 'City of the register record',
                                                                         'url': src_url, 'kind': 'contract_register' if t['country'] == 'US' else 'legal_entity_identifier_reference'}],
                                                              'claims': [{'company_id': t['id'], 'ref': 'b4:city', 'path': '/identity/city', 'value': town,
                                                                          'assessment': 'registry_supported', 'srcs': ['b4:city_src']}]}),
                                               'day': today.isoformat(), 'note': note})
            if found:
                subs = conn.execute(SUBS_SQL, {'names': [n.lower() for n in names + ([ident_name] if ident_name else [])]}).fetchall()
                found.update(sub_claims(subs))
                core = None
                if not t['has_card']:
                    idn = identity(t, d) if defence else None
                    if not idn:
                        outcome, reason = 'no_identity', 'no card: ' + ('no LEI or UEI' if defence else 'no defence buyer on record')
                        found = {}
                    else:
                        why = ('US Department of Defense contracts in USAspending' if t['country'] == 'US'
                               else 'contracts with a Canadian defence buyer in Open Government Canada')
                        core = {'id': idn, 'legal_name': ident_name or t['name'], 'why': why, 'city': town}
                if found:
                    now = conn.execute(NOW_SQL, {'co': t['id']}).fetchall()
                    if now and same(now, found):
                        outcome, reason = 'unchanged', 'same contract records'
                    else:
                        data = payload(t, found, today, core)
                        if write:
                            with conn.transaction():
                                if now:
                                    conn.execute(RETIRE_SQL, {'co': t['id'], 'day': today.isoformat()})
                                conn.execute(A.WRITE_SQL, {'j': json.dumps(data, ensure_ascii=False), 'day': today.isoformat(), 'note': note})
                        outcome = ('card_made' if core else 'refreshed' if now else 'written') + ('' if write else '_dry')
                        reason = f'{len(data["claims"])} claims'
                        if core:
                            summary['cards_made'].append(f"{t['country']} {t['name']}")
        except Exception as e:  # noqa: BLE001 — a source down for one company never stops the pass
            outcome, reason = 'error', type(e).__name__
        if write:
            try:  # the attempt row only schedules the next check: a failure here never stops the step
                with conn.transaction():
                    conn.execute(ATTEMPT_SQL, (t['id'], run_label, DB_OUTCOME.get(outcome, 'error'),
                                               'L3' if outcome.startswith('card_made') else None, f'{outcome}: {reason}'[:480] + MARK))
            except Exception as e:  # noqa: BLE001
                summary['outcomes']['attempt_not_logged'] = summary['outcomes'].get('attempt_not_logged', 0) + 1
                log(f'attempt not logged: {type(e).__name__}')
        summary['outcomes'][outcome] = summary['outcomes'].get(outcome, 0) + 1
        log(f"{t['country']} {t['name'][:40]:40} -> {outcome} {reason}")
    return summary
