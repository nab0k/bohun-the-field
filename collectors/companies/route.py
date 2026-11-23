"""Nightly route step: LEI, Wikidata QID and country for the companies without a national register route
(B4CE-04, owner 23.11.2026 «Да, делай»; EXP-033 DRY_RUN_WIKIDATA_GLEIF_20261123.md).

A found LEI puts the company into the 'lei' tier of the autofill queue, which then checks the LEI again in GLEIF
by the exact name (resolve_lei). Writes only identifiers (scheme LEI / WIKIDATA) and a missing country. A value
already held by another company is not moved ('taken'). One route_attempt row per company and run.
"""
import time
from datetime import date

from .sources import wikidata as W

REGISTER_COUNTRIES = ('CZ', 'NO', 'FR', 'FI', 'PL')  # these have their own register route in autofill
RETRY_DAYS = 60
SOURCE = 'B4CE-04 route {day}: {what}'

CANDIDATES_SQL = """select c.id, c.name, c.country,
  exists (select 1 from companies.signal s where s.company_id = c.id) as has_signal
from companies.company c
where c.excluded_reason is null
  and (c.country is null or c.country <> all(%(register)s))
  and not exists (select 1 from companies.company_identifier i where i.company_id = c.id and i.scheme in ('LEI', 'WIKIDATA'))
  and not exists (select 1 from companies.route_attempt a where a.company_id = c.id
                  and a.attempted_at > now() - make_interval(days => %(retry)s))
order by has_signal desc, c.id
limit %(limit)s"""
INSERT_ID_SQL = """insert into companies.company_identifier (scheme, value, company_id, source, observed_at)
values (%(scheme)s, %(value)s, %(company_id)s, %(source)s, now()) on conflict (scheme, value) do nothing
returning company_id"""
COUNTRY_SQL = """update companies.company set country = %(country)s, updated_at = now()
where id = %(company_id)s and country is null"""
ATTEMPT_SQL = """insert into companies.route_attempt (company_id, run, outcome, lei, qid, country, reason)
values (%(company_id)s, %(run)s, %(outcome)s, %(lei)s, %(qid)s, %(country)s, %(reason)s)"""


def outcome(company, found):
    """(outcome, lei, qid, country, reason) for one lookup result of enrich()."""
    wd, lei = found['wikidata'], found['lei']
    qid = wd.get('qid') if wd.get('decision') == 'matched' else None
    if wd.get('decision') == 'error' and lei.get('decision') == 'error':
        return 'error', None, None, None, f"{wd.get('reason')}; {lei.get('reason')}"
    country = None if company.get('country') else found.get('country')
    if lei.get('decision') == 'conflict':
        return 'conflict', None, qid, country, 'two LEIs: ' + ', '.join(lei['candidates'])
    code = lei.get('lei') if lei.get('decision') in ('matched', 'from_wikidata') else None
    if code or qid or country:
        return 'found', code, qid, country, lei.get('decision')
    return 'not_found', None, None, None, f"wikidata {wd.get('decision')}, gleif {lei.get('decision')}"


def run(conn, write=False, limit=300, minutes=50, today=None, enrich=W.enrich, log=print):
    """conn: psycopg connection, autocommit, dict rows. One transaction per company when writing."""
    today = today or date.today()
    label = f'B4CE-04 route {today.isoformat()}'
    started = time.monotonic()
    rows = conn.execute(CANDIDATES_SQL, {'register': list(REGISTER_COUNTRIES), 'retry': RETRY_DAYS,
                                         'limit': limit}).fetchall()
    summary = {'run': label, 'chosen': len(rows), 'outcomes': {}, 'lei': 0, 'qid': 0, 'country': 0, 'taken': 0}
    for c in rows:
        if time.monotonic() - started > minutes * 60:
            log('time limit reached')
            break
        out, lei, qid, country, reason = outcome(c, enrich(c['name'], c['country']))
        if write:
            with conn.transaction():
                for scheme, value in (('LEI', lei), ('WIKIDATA', qid)):
                    if not value:
                        continue
                    what = 'GLEIF name search' if scheme == 'LEI' and reason == 'matched' else 'Wikidata'
                    got = conn.execute(INSERT_ID_SQL, {'scheme': scheme, 'value': value, 'company_id': c['id'],
                                                       'source': SOURCE.format(day=today, what=what)}).fetchone()
                    if not got:  # the value belongs to another record already: a possible duplicate, not moved
                        summary['taken'] += 1
                        out, reason = 'taken', f'{scheme} {value} already on another company'
                if country:
                    conn.execute(COUNTRY_SQL, {'country': country, 'company_id': c['id']})
                conn.execute(ATTEMPT_SQL, {'company_id': c['id'], 'run': label, 'outcome': out, 'lei': lei,
                                           'qid': qid, 'country': country, 'reason': reason})
        summary['outcomes'][out] = summary['outcomes'].get(out, 0) + 1
        summary['lei'] += bool(lei)
        summary['qid'] += bool(qid)
        summary['country'] += bool(country)
    return summary
