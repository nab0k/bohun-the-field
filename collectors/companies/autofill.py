"""Autonomous card filling, design B v1 slice 3 step 3.5 (B4CE-04, EXP-033).

One run takes the next companies from the work queue (queue.build), builds each card exactly as the
manual batches did (registries -> stored signals -> website products -> level -> gate, card_run.card) and
writes the cards that pass to companies.profile_source / profile_claim / profile_claim_source in the same
claim format (one transaction per company). Nothing is published: public_release stays false and the site
is rebuilt by its own job, released only on the owner's command (decision 21.11.2026).

Owner review of the manual batches is replaced by code rules learned from it:
  - the card gate of design B v1 (legal entity matched, level backed by an independent source or honest L0/L4);
  - L0 is not written (owner, Finland 22.11.2026: association membership alone is not a card);
  - a register activity that is plainly not industrial (food service, lodging, personal or household
    services) is held for review: a name match there is the AddUp trap (N08);
  - a company that already has an approved card is not rewritten, nor is a second record of a legal entity
    (same register number) that already has one.
Every attempt, written or not, is recorded in companies.card_attempt with its reason; a company is not
tried again for RETRY_DAYS unless a new event names it.
"""
import json
import re
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import products as P
from .normalize import name_norm
from .card_run import SIGNALS_SQL, card
from .queue import EXPORT_SQL, build
from .sources import registries as R
from .sources.website import read_site

ROUTES = ('open_register', 'lei')
RETRY_DAYS = 30
# NACE / NAF / PKD divisions of activities that are not defence industry in any reading.
HOLD_DIVISIONS = {'55': 'lodging', '56': 'food service', '96': 'personal services', '97': 'household employer',
                  '98': 'household production'}
# Owner 23.11.2026: universities are not company cards; they are a separate category for later (defence cooperation).
UNIVERSITY = re.compile(r'univerzit|universit|uniwersytet|yliopisto|universitet|universidad|universidade', re.I)
WHITELIST = 'https://wl-api.mf.gov.pl/api/search/nip/{nip}?date={day}'
FEED = 'https://bohundefence.com/feed/synthesis-feed.json'
REG_KIND = {'LEI': 'legal_entity_identifier_reference'}
REG_TITLE = {'FR_SIREN': 'recherche-entreprises (French register)', 'NO_ORGNR': 'Brønnøysund register',
             'FI_YTUNNUS': 'PRH register', 'CZ_ICO': 'ARES (Czech register)', 'PL_KRS': 'KRS (Polish register)',
             'LEI': 'GLEIF LEI record'}

ATTEMPTS_SQL = """select company_id, max(attempted_at)::date::text as last from companies.card_attempt group by company_id"""
DETAILS_SQL = """select c.id, c.website,
  coalesce((select jsonb_object_agg(i.scheme, i.value) from companies.company_identifier i where i.company_id = c.id), '{}'::jsonb) as ids
from companies.company c where c.id = any(%(ids)s)"""
WRITE_SQL = """with p as (select %(j)s::jsonb j),
src as (insert into companies.profile_source (company_id,source_ref,title,source_url,source_kind,as_of)
  select s->>'company_id', s->>'ref', s->>'title', s->>'url', s->>'kind', %(day)s::date from p, jsonb_array_elements(j->'sources') s
  returning id, company_id, source_ref),
cl as (insert into companies.profile_claim (company_id,claim_ref,field_path,claim_value,assessment,review_note,content_sha256,qa_status,public_release)
  select c->>'company_id', c->>'ref', c->>'path', c->'value', c->>'assessment', %(note)s,
  encode(sha256(convert_to((c->'value')::text,'UTF8')),'hex'), 'APPROVED', false from p, jsonb_array_elements(j->'claims') c
  returning id, company_id, claim_ref)
insert into companies.profile_claim_source (company_id, claim_id, source_id)
select distinct cl.company_id, cl.id, src.id from p, jsonb_array_elements(j->'claims') c
join cl on cl.company_id=c->>'company_id' and cl.claim_ref=c->>'ref'
join src on src.company_id=cl.company_id and src.source_ref in (select jsonb_array_elements_text(c->'srcs'))"""
CHECK_SQL = """select (select count(*) from companies.profile_claim where company_id=%(co)s and review_note=%(note)s) as claims,
  (select count(*) from companies.profile_claim where company_id=%(co)s and qa_status='APPROVED' and review_note<>%(note)s) as other"""
# The same legal entity may sit under two catalogue records (e.g. "FORCIT" and "Oy Forcit Ab"): one card only.
SAME_ENTITY_SQL = """select company_id from companies.profile_claim where claim_ref = 'b4:regno' and qa_status = 'APPROVED'
  and claim_value = %(v)s::jsonb and company_id <> %(co)s limit 1"""
# Owner 23.11.2026 (duplicate cleanup): a carded company under another record is found by its legal name and country
# too, not only by the same register number — Česká zbrojovka had a card by IČO and got a second one by LEI.
CARDED_NAMES_SQL = """select p.company_id, p.claim_value #>> '{}' as legal_name, c.country
from companies.profile_claim p join companies.company c on c.id = p.company_id
where p.claim_ref = 'b4:legal_name' and p.qa_status = 'APPROVED'"""
ATTEMPT_SQL = """insert into companies.card_attempt (company_id, run, outcome, level, reason) values (%s, %s, %s, %s, %s)"""


# --- selection ------------------------------------------------------------------------------------------
def select(queue, attempts, today, limit, countries=None, with_signal=None):
    """Next companies to try: a free register route, no approved card, not tried within RETRY_DAYS
    (an event newer than the last attempt brings it back). Within the queue order, events come first, then
    companies with a grant or award (`with_signal`): without one a card can only be L0, which is not written."""
    since = (today - timedelta(days=RETRY_DAYS)).isoformat()
    with_signal = with_signal or set()
    out = []
    for q in sorted(queue, key=lambda q: (q['tier'] != 'event', q['company_id'] not in with_signal)):
        if q['route'] not in ROUTES or q['refresh']:
            continue
        if countries and q['country'] not in countries:
            continue
        last = attempts.get(q['company_id'])
        if last and last >= since and not (q['tier'] == 'event' and (q['last_event'] or '') > last):
            continue
        out.append(q)
        if len(out) >= limit:
            break
    return out


# --- identity -------------------------------------------------------------------------------------------
def krs_from_nip(nip, today, opener=urllib.request.urlopen):
    """KRS number for a Polish NIP from the Ministry of Finance VAT whitelist (free, no key)."""
    nip = re.sub(r'\D', '', nip or '')
    if len(nip) != 10:
        return None
    try:
        with opener(WHITELIST.format(nip=nip, day=today.isoformat()), timeout=30) as r:
            data = json.loads(r.read().decode('utf-8', 'replace'))
    except Exception:  # noqa: BLE001 — no number is an honest "not found"
        return None
    return ((data.get('result') or {}).get('subject') or {}).get('krs') or None


def identity(entry, ids, today, opener=urllib.request.urlopen):
    name, country = entry['name'], entry['country']
    if country == 'PL':
        krs = ids.get('KRS') or krs_from_nip(ids.get('NATIONAL:PL'), today, opener)
        if not krs:
            return {'decision': 'not_found', 'nearest': [],
                    'reason': 'no KRS number for this NIP in the VAT whitelist (sole trader or not registered in KRS)'}
        return R.resolve(name, 'PL', opener, registered_as=krs)
    if entry['route'] == 'lei' and ids.get('LEI'):
        return R.resolve_lei(name, ids['LEI'], opener)
    try:
        res = R.resolve(name, country, opener)
    except Exception:  # noqa: BLE001 — one retry after a pause, then an error record
        time.sleep(10)
        try:
            res = R.resolve(name, country, opener)
        except Exception as e:  # noqa: BLE001
            return {'decision': 'error', 'reason': type(e).__name__}
    if res.get('decision') != 'matched' and ids.get('LEI'):
        by_lei = R.resolve_lei(name, ids['LEI'], opener)  # same exact-name rule, legal name from GLEIF
        if by_lei.get('decision') == 'matched':
            return by_lei
    return res


def worth_site(signals):
    """Website and products only where a card can rise above L0 under the gate."""
    return any(s['type'] in ('grant', 'tender_award', 'contract', 'self_declared') for s in signals)


def products_for(entry, site, provider, pause=5):
    """provider: one provider tuple or a chain of them. A provider that fails (429 quota, 402 payment, 5xx) hands
    the company to the next one (owner 23.11.2026: work without the paid Gemini key)."""
    if not site['pages'] or not provider:
        return None
    chain = [provider] if isinstance(provider, tuple) else list(provider)
    last = None
    for p in chain:
        try:
            return P.extract(entry, site['pages'], p)
        except urllib.error.HTTPError as e:
            last = f'error HTTP {e.code}'
        except Exception as e:  # noqa: BLE001
            last = f'error {type(e).__name__}'
        finally:
            time.sleep(pause)
    return {'kept': [], 'rejected': [], 'provider': last or 'error no provider'}


# --- decision -------------------------------------------------------------------------------------------
def held_activity(ident, name=''):
    names = ' '.join(filter(None, [name, ident.get('legal_name')]))
    if UNIVERSITY.search(names) or any(re.sub(r'\D', '', str(c)).startswith('854') for c in ident.get('activity') or []):
        return 'university: not a company card, deferred category (owner 23.11.2026)'
    for code in ident.get('activity') or []:
        digits = re.sub(r'\D', '', str(code))
        if len(digits) >= 2 and digits[:2] in HOLD_DIVISIONS:
            return f'register activity {code} ({HOLD_DIVISIONS[digits[:2]]}): name match needs review (N08)'
    return None


def decide(c):
    """(outcome, reason) for one card."""
    if c['identity'].get('decision') == 'error':
        return 'error', 'register: ' + str(c['identity'].get('reason'))
    if c['gate'] != 'pass':
        return 'fail', '; '.join(c['gaps'][:2]) or 'gate: no independent source for the level'
    if c['level'] == 'L0':
        return 'held_l0', 'L0: association membership only (owner decision 22.11.2026)'
    hold = held_activity(c['identity'], c.get('name') or '')
    if hold:
        return 'held_activity', hold
    return 'write', f"{c['level']} {c['confidence']} {c['rule']}"


# --- claims (same format as the manual batches, reference/slice3/db_payload.py) ----------------------------
def payload(c, run):
    sources, claims = [], []
    co, idn = c['company_id'], c['identity']

    def src(ref, title, url, kind):
        sources.append({'company_id': co, 'ref': ref, 'title': title, 'url': url, 'kind': kind})
        return ref

    def claim(ref, path, value, assessment, srcs):
        claims.append({'company_id': co, 'ref': ref, 'path': path, 'value': value, 'assessment': assessment, 'srcs': srcs})

    reg = src('b4:reg', REG_TITLE[idn['scheme']], idn['source_url'], REG_KIND.get(idn['scheme'], 'government_register'))
    claim('b4:legal_name', '/identity/legal_name', idn['legal_name'], 'registry_supported', [reg])
    claim('b4:regno', '/identity/registration_number', {'scheme': idn['scheme'], 'value': idn['id']}, 'registry_supported', [reg])
    for key, path in (('founded', '/identity/founded'), ('city', '/identity/city'), ('activity', '/identity/activity_codes')):
        if idn.get(key):
            claim('b4:' + key, path, idn[key], 'registry_supported', [reg])
    ev_refs = []
    for n, e in enumerate(c['evidence'], 1):
        if e['type'] == 'grant':
            pid = re.search(r'/(\d{9})/EDF', e['url'])
            ref_id = pid.group(1) if pid else e['url'].rsplit('/', 1)[-1]
            kind, title, skind = 'edf_grant', f'EDF project {ref_id}', 'eu_programme_record'
        elif 'usaspending.gov' in e['url']:
            ref_id = e['url'].split('/recipient/')[1].split('/')[0]
            kind, title, skind = 'us_federal_contract', 'USAspending recipient profile', 'contract_register'
        else:
            ref_id = e['url'].rsplit('/', 1)[-1]
            kind, title, skind = 'tender_award', f'TED award {ref_id}', 'contract_register'
        r = src(f'b4:ev:{n}', title, e['url'], skind)
        ev_refs.append(r)
        claim(f'b4:prog{n}', '/programmes/-', {'kind': kind, 'ref': ref_id, 'counterpart': e['counterpart'], 'date': e['date'],
              'defence_buyer': e['layer'] == 'A', 'url': e['url']}, 'cross_source', [r])
    page_refs = {}
    for n, p in enumerate(c['products'], 1):
        if p['url'] not in page_refs:
            page_refs[p['url']] = src(f'b4:page:{len(page_refs) + 1}', 'Company website', p['url'], 'company_website')
        claim(f'b4:prod{n}', '/products/-', {'name': p['name'], 'kind': p['kind'], 'specialized': p['specialized'],
              'quote': p['quote'], 'url': p['url']}, 'company_claim', [page_refs[p['url']]])
    lvl_srcs = ev_refs + ([page_refs[p['url']] for p in c['products'] if p['specialized']] if c['level'] in ('L1', 'L2') else [])
    claim('b4:level', '/classification/defence_level', {'level': c['level'], 'confidence': c['confidence'], 'rule': c['rule'],
          'explanation': c['explanation'], 'ignored': c['ignored'], 'classifier': 'collectors/companies/level.py', 'run': run},
          'cross_source' if ev_refs else 'company_claim', sorted(set(lvl_srcs)) or [reg])
    claim('b4:empty', '/gaps', {**c['empty'], 'notes': c['gaps']}, 'inferred_needs_review', [reg])
    keys = [(s['ref'], s['url']) for s in sources]
    assert len(keys) == len(set(keys)) and len({x['ref'] for x in claims}) == len(claims)
    assert all(s['url'].startswith('https://') for s in sources)
    return {'sources': sources, 'claims': claims}


# --- run ------------------------------------------------------------------------------------------------
def load_feed(opener=urllib.request.urlopen):
    try:
        with opener(urllib.request.Request(FEED, headers={'User-Agent': 'BOHUN-research/1.0'}), timeout=30) as r:
            return json.loads(r.read().decode('utf-8')).get('items') or []
    except Exception:  # noqa: BLE001 — without the feed the queue simply has no event tier
        return []


def gemini_provider(env):
    key = env.get('GEMINI_API_KEY') or env.get('GEMINI_FREE_API_KEY')
    return ('gemini', None, key, env.get('PRODUCTS_GEMINI_MODEL', 'gemini-3.5-flash-lite')) if key else None


def model_chain(env):
    """Free providers first (free Gemini, Groq, Mistral — products.providers()), the paid Gemini key last: it answered
    402 «prepayment credits are depleted» on 23.11.2026 and must not block the products of a card."""
    chain = P.providers()
    paid = env.get('GEMINI_API_KEY')
    if paid and not any(p[2] == paid for p in chain):
        chain.append(('gemini', None, paid, env.get('PRODUCTS_GEMINI_MODEL', 'gemini-3.5-flash-lite')))
    return chain


def run(conn, env, limit=40, countries=None, write=False, today=None, minutes=50, log=print):
    """conn: psycopg connection with dict rows. Returns the run summary."""
    today = today or date.today()
    started = time.monotonic()
    run_label = f'B4CE-04 auto {today.isoformat()}'
    note = f'{run_label}: card passed the code gate of design B v1, written without manual review (EXP-033 step 3.5)'
    companies = conn.execute(EXPORT_SQL).fetchall()
    queue, counts = build(companies, load_feed(), today)
    attempts = {r['company_id']: r['last'] for r in conn.execute(ATTEMPTS_SQL).fetchall()}
    chosen = select(queue, attempts, today, limit, countries, {c['id'] for c in companies if c.get('last_signal')})
    log(f'queue {counts}; chosen {len(chosen)}; write={write}')
    ids_ = [q['company_id'] for q in chosen]
    details = {r['id']: r for r in conn.execute(DETAILS_SQL, {'ids': ids_}).fetchall()} if ids_ else {}
    signals = {}
    for s in (conn.execute(SIGNALS_SQL, {'ids': ids_}).fetchall() if ids_ else []):
        signals.setdefault(s['company_id'], []).append(dict(s))
    carded = {}
    for r in conn.execute(CARDED_NAMES_SQL).fetchall():
        if r['legal_name']:
            carded.setdefault((name_norm(r['legal_name']), r['country']), r['company_id'])
    provider = model_chain(env)
    summary = {'run': run_label, 'chosen': len(chosen), 'outcomes': {}, 'written': [], 'held': [], 'claims': 0}
    conn.commit()
    for q in chosen:
        if time.monotonic() - started > minutes * 60:
            log('time budget reached')
            break
        d = details.get(q['company_id']) or {}
        ids = d.get('ids') or {}
        sig = signals.get(q['company_id'], [])
        entry = {'id': q['company_id'], 'name': q['name'], 'country': q['country'], 'route': q['route']}
        ident = identity(entry, ids, today)
        website = d.get('website')
        if website and worth_site(sig) and ident.get('decision') == 'matched':
            site = read_site(website)
        else:
            site = {'pages': [], 'skipped': [(None, 'no website in the record' if not website
                                              else 'site not read: no defence evidence beyond association membership')]}
        prods = products_for(entry, site, provider)
        c = card(entry, ident, sig, prods, site, ids)
        if prods and str(prods.get('provider') or '').startswith('error'):
            c['empty']['products'] = 'not_assessed'
            c['gaps'].append(f"products: model {prods['provider']}")
        outcome, reason = decide(c)
        if outcome == 'write':
            regno = json.dumps({'scheme': ident['scheme'], 'value': ident['id']})
            twin = conn.execute(SAME_ENTITY_SQL, {'v': regno, 'co': c['company_id']}).fetchone()
            twin = twin['company_id'] if twin else carded.get((name_norm(ident.get('legal_name') or ''), entry['country']))
            if twin and twin != c['company_id']:
                outcome, reason = 'skipped', f"same legal entity already has a card ({twin})"
        if outcome == 'write':
            data = payload(c, run_label)
            if write:
                with conn.transaction():
                    have = conn.execute(CHECK_SQL, {'co': c['company_id'], 'note': note}).fetchone()
                    if have['other'] or have['claims']:
                        outcome, reason = 'skipped', 'approved claims already present'
                    else:
                        conn.execute(WRITE_SQL, {'j': json.dumps(data, ensure_ascii=False), 'day': today.isoformat(), 'note': note})
                        got = conn.execute(CHECK_SQL, {'co': c['company_id'], 'note': note}).fetchone()['claims']
                        if got != len(data['claims']):
                            raise RuntimeError(f"{c['company_id']}: {got} claims stored, {len(data['claims'])} expected")
            if outcome == 'write':
                outcome = 'written' if write else 'would_write'
                summary['claims'] += len(data['claims'])
                summary['written'].append(f"{c['country']} {c['name']} {c['level']}")
        elif outcome.startswith('held') or outcome == 'skipped':
            summary['held'].append(f"{c['country']} {c['name']}: {reason}")
        if write:
            with conn.transaction():
                conn.execute(ATTEMPT_SQL, (c['company_id'], run_label, outcome, c['level'], reason[:500]))
        summary['outcomes'][outcome] = summary['outcomes'].get(outcome, 0) + 1
        # The log is public: a name is shown only for a legal entity matched in a register (a sole trader's
        # name is a person's name and stays out, e.g. Polish entries without KRS).
        shown = c['name'][:40] if ident.get('decision') == 'matched' else c['company_id']
        log(f"{c['country']} {shown:40} id={ident.get('decision', '?')[:9]:9} -> {c['level']} {c['confidence']} {outcome}")
    summary['finished_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    return summary
