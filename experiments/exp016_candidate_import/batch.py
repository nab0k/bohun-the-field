"""One reviewed batch of candidate companies: insert, check and undo. No fetching, matching or promotion.

`conn` only needs execute(sql, params) with %s parameters, so tests run the same statements on SQLite.
Tables are unqualified: the caller sets search_path to `companies`.
"""
import hashlib
import json

BATCH = 'C2-08-20261105'
ID_PREFIX = 'co_c208_'
SOURCE = 'c2_08_batch_20261105'
FLAG = 'BATCH:' + BATCH
TABLES = ('company', 'company_identifier', 'company_alias', 'company_category', 'signal', 'observation')
SIGNAL_TYPES = {'grant', 'tender_award', 'subcontract'}


class BatchError(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise BatchError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(packet):
    require(packet.get('format') == 'exp016.candidate_batch.v1' and packet.get('batch') == BATCH, 'PACKET_FORMAT')
    companies = packet.get('companies')
    require(isinstance(companies, list) and companies, 'PACKET_EMPTY')
    ids, idents = set(), set()
    for c in companies:
        require(isinstance(c.get('id'), str) and c['id'].startswith(ID_PREFIX) and c['id'] not in ids, 'COMPANY_ID')
        ids.add(c['id'])
        require(isinstance(c.get('name'), str) and c['name'].strip() and c.get('name_norm'), 'COMPANY_NAME')
        require(c.get('status', 'candidate') == 'candidate', 'STATUS_ONLY_CANDIDATE')
        require(c.get('country') not in ('RU', 'CN', 'BY', 'UA'), 'COUNTRY_DENIED')
        require('website' not in c, 'WEBSITE_NOT_IN_THIS_BATCH')
        require(FLAG in c.get('flags', []), 'BATCH_FLAG')
        require(c.get('layer') in ('A', 'B', 'C', 'D'), 'LAYER')
        require(c.get('signals'), 'SIGNAL_REQUIRED')
        for s in c['signals']:
            require(s.get('type') in SIGNAL_TYPES and s.get('layer') in ('A', 'B', 'C', 'D'), 'SIGNAL')
        for i in c.get('identifiers', []):
            key = (i['scheme'], i['value'])
            require(key not in idents, 'IDENTIFIER_DUPLICATE_IN_PACKET')
            idents.add(key)
    return packet


def fingerprint(conn):
    rows = conn.execute('SELECT id FROM company ORDER BY id').fetchall()
    return {'companies': len(rows), 'ids_md5': hashlib.md5(','.join(r[0] for r in rows).encode()).hexdigest()}


def counts(conn):
    out = {t: conn.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in TABLES}
    out['batch_companies'] = conn.execute('SELECT count(*) FROM company WHERE id LIKE %s', (ID_PREFIX + '%',)).fetchone()[0]
    out['batch_observations'] = conn.execute('SELECT count(*) FROM observation WHERE source=%s', (SOURCE,)).fetchone()[0]
    return out


def precheck(conn, expected):
    now = fingerprint(conn)
    require(now == expected, 'REGISTRY_CHANGED')
    state = counts(conn)
    require(state['batch_companies'] == 0 and state['batch_observations'] == 0, 'BATCH_ALREADY_PRESENT')
    return state


def apply(conn, packet, at):
    """Inserts every company whose identifiers are free. A taken identifier skips that company whole."""
    report = {'companies': 0, 'identifiers': 0, 'aliases': 0, 'categories': 0, 'signals': 0, 'observations': 0, 'skipped_identifier_conflict': 0}
    for c in validate(packet)['companies']:
        taken = any(conn.execute('SELECT 1 FROM company_identifier WHERE scheme=%s AND value=%s', (i['scheme'], i['value'])).fetchone()
                    for i in c.get('identifiers', []))
        if taken:
            report['skipped_identifier_conflict'] += 1
            continue
        conn.execute("""INSERT INTO company(id,name,name_norm,country,city,flags,layer,status,created_at,updated_at)
                        VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s,'candidate',%s,%s)""",
                     (c['id'], c['name'], c['name_norm'], c.get('country'), c.get('city'), json.dumps(sorted(set(c['flags'])), ensure_ascii=False), c['layer'], at, at))
        report['companies'] += 1
        for i in c.get('identifiers', []):
            conn.execute('INSERT INTO company_identifier(scheme,value,company_id,source,observed_at) VALUES(%s,%s,%s,%s,%s)',
                         (i['scheme'], i['value'], c['id'], SOURCE, at))
            report['identifiers'] += 1
        for a in c.get('aliases', []):
            conn.execute('INSERT INTO company_alias(company_id,alias,alias_norm,source) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                         (c['id'], a['alias'], a['alias_norm'], SOURCE))
            report['aliases'] += 1
        for k in c.get('categories', []):
            conn.execute('INSERT INTO company_category(company_id,axis,value,basis) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                         (c['id'], k['axis'], k['value'], k.get('basis')))
            report['categories'] += 1
        for n, s in enumerate(c['signals']):
            payload = {'batch': BATCH, 'company': c['id'], 'origin': s.get('origin'), 'url': s.get('url'), 'status': c.get('review_status')}
            if n == 0 and c.get('website_observed'):
                payload['website_observed'] = c['website_observed']
            key = digest([c['id'], s['type'], s.get('counterpart'), s.get('date'), s.get('url'), n])
            row = conn.execute("""INSERT INTO observation(source,source_key,content_hash,fetched_at,payload)
                                  VALUES(%s,%s,%s,%s,%s::jsonb) RETURNING id""",
                               (SOURCE, key, digest(payload), at, json.dumps(payload, ensure_ascii=False))).fetchone()
            report['observations'] += 1
            conn.execute('INSERT INTO signal(company_id,type,layer,counterpart,signal_date,url,observation_id) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                         (c['id'], s['type'], s['layer'], s.get('counterpart'), s.get('date'), s.get('url'), row[0]))
            report['signals'] += 1
    return report


def verify(before, after, report):
    expected = {'company': 'companies', 'company_identifier': 'identifiers', 'signal': 'signals', 'observation': 'observations'}
    for table, field in expected.items():
        require(after[table] - before[table] == report[field], 'COUNT_MISMATCH_' + table.upper())
    require(after['batch_companies'] == report['companies'] and after['batch_observations'] == report['observations'], 'BATCH_COUNT_MISMATCH')


def undo(conn, packet):
    """Removes only this batch. Anything referencing a batch company outside these tables aborts the transaction."""
    limit = len(validate(packet)['companies'])
    state = counts(conn)
    require(state['batch_companies'] <= limit, 'MORE_THAN_THE_BATCH')
    conn.execute('DELETE FROM signal WHERE company_id LIKE %s', (ID_PREFIX + '%',))
    conn.execute('DELETE FROM company_category WHERE company_id LIKE %s', (ID_PREFIX + '%',))
    conn.execute('DELETE FROM company_alias WHERE company_id LIKE %s', (ID_PREFIX + '%',))
    conn.execute('DELETE FROM company_identifier WHERE company_id LIKE %s', (ID_PREFIX + '%',))
    conn.execute('DELETE FROM company WHERE id LIKE %s', (ID_PREFIX + '%',))
    conn.execute('DELETE FROM observation WHERE source=%s', (SOURCE,))
    after = counts(conn)
    require(after['batch_companies'] == 0 and after['batch_observations'] == 0, 'UNDO_INCOMPLETE')
    return {'removed_companies': state['batch_companies'], 'removed_observations': state['batch_observations']}
