"""One reviewed batch of C2-09 company descriptions: insert, check and undo (C2-10, D-20261113-C2-10-PATH-A).

Writes only companies.profile_source, profile_claim and profile_claim_source for existing C2-08 candidates.
Every claim stays qa_status HOLD and public_release false; nothing here publishes or edits a company row.
`conn` only needs execute(sql, params) with %s parameters, so tests run the same statements on SQLite.
Tables are unqualified: the caller sets search_path to `companies`.
"""
import hashlib
import json
import re

BATCH = 'C2-10-20261113'
RUN_ID = '43185650577'
ID_PREFIX = 'co_c208_'
SOURCE_REF = 'c209_home'
CLAIM_PREFIX = 'c209_summary_'
FIELD_PATH = '/summary/primary_activity'
MAX_CLAIMS = 3
TABLES = ('profile_source', 'profile_claim', 'profile_claim_source')


class BatchError(RuntimeError):
    pass


def require(ok, code):
    if not ok:
        raise BatchError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate(packet):
    require(packet.get('format') == 'exp016.c2_10.descriptions.v1' and packet.get('run_id') == RUN_ID, 'PACKET_FORMAT')
    companies = packet.get('companies')
    require(isinstance(companies, list) and companies, 'PACKET_EMPTY')
    ids = set()
    for c in companies:
        cid = c.get('company_id')
        require(isinstance(cid, str) and cid.startswith(ID_PREFIX) and cid not in ids, 'COMPANY_ID')
        ids.add(cid)
        src = c.get('source') or {}
        require(src.get('source_ref') == SOURCE_REF and src.get('source_kind') == 'company_website', 'SOURCE_KIND')
        require(isinstance(src.get('url'), str) and re.fullmatch(r'https://\S{1,2000}', src['url']) is not None, 'SOURCE_URL')
        require(re.fullmatch(r'\d{4}-\d{2}-\d{2}', src.get('as_of', '')) is not None and src.get('title'), 'SOURCE_META')
        claims = c.get('claims')
        require(isinstance(claims, list) and 0 < len(claims) <= MAX_CLAIMS, 'CLAIM_COUNT')
        refs = set()
        for cl in claims:
            require(re.fullmatch(re.escape(CLAIM_PREFIX) + r'[1-9]', cl.get('claim_ref', '')) is not None
                    and cl['claim_ref'] not in refs, 'CLAIM_REF')
            refs.add(cl['claim_ref'])
            require(cl.get('field_path') == FIELD_PATH and cl.get('assessment') == 'company_claim', 'CLAIM_KIND')
            value, quote = cl.get('value'), cl.get('quote')
            require(isinstance(value, str) and 0 < len(value.strip()) <= 400, 'CLAIM_VALUE')
            require(isinstance(quote, str) and 0 < len(quote.strip()) <= 400, 'CLAIM_QUOTE')
            require(cl.get('content_sha256') == digest([cid, FIELD_PATH, value]), 'CLAIM_HASH')
    return packet


def counts(conn):
    out = {t: conn.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in TABLES}
    out['batch_sources'] = conn.execute('SELECT count(*) FROM profile_source WHERE source_ref=%s AND company_id LIKE %s',
                                        (SOURCE_REF, ID_PREFIX + '%')).fetchone()[0]
    out['batch_claims'] = conn.execute('SELECT count(*) FROM profile_claim WHERE claim_ref LIKE %s AND company_id LIKE %s',
                                       (CLAIM_PREFIX + '%', ID_PREFIX + '%')).fetchone()[0]
    return out


def precheck(conn, packet):
    """Every target company must exist as a candidate; the batch must not be present yet."""
    state = counts(conn)
    require(state['batch_sources'] == 0 and state['batch_claims'] == 0, 'BATCH_ALREADY_PRESENT')
    missing = 0
    for c in validate(packet)['companies']:
        row = conn.execute('SELECT status FROM company WHERE id=%s', (c['company_id'],)).fetchone()
        missing += row is None or row[0] != 'candidate'
    require(missing == 0, 'TARGET_COMPANY_MISSING_OR_NOT_CANDIDATE')
    return state


def note(cl):
    return json.dumps({'batch': BATCH, 'run_id': RUN_ID, 'quote': cl['quote'],
                       'basis': 'model summary of the home page; quote checked against the page text'},
                      ensure_ascii=False, sort_keys=True)


def apply(conn, packet, at):
    report = {'sources': 0, 'claims': 0, 'links': 0}
    for c in validate(packet)['companies']:
        s = c['source']
        source_id = conn.execute("""INSERT INTO profile_source(company_id,source_ref,title,source_url,source_kind,as_of,created_at)
                                    VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                                 (c['company_id'], SOURCE_REF, s['title'], s['url'], s['source_kind'], s['as_of'], at)).fetchone()[0]
        report['sources'] += 1
        for cl in c['claims']:
            claim_id = conn.execute("""INSERT INTO profile_claim(company_id,claim_ref,field_path,claim_value,assessment,review_note,
                                       content_sha256,qa_status,public_release,created_at)
                                       VALUES(%s,%s,%s,%s::jsonb,%s,%s,%s,'HOLD',false,%s) RETURNING id""",
                                    (c['company_id'], cl['claim_ref'], FIELD_PATH, json.dumps(cl['value'], ensure_ascii=False),
                                     'company_claim', note(cl), cl['content_sha256'], at)).fetchone()[0]
            report['claims'] += 1
            conn.execute('INSERT INTO profile_claim_source(company_id,claim_id,source_id) VALUES(%s,%s,%s)',
                         (c['company_id'], claim_id, source_id))
            report['links'] += 1
    return report


def verify(before, after, report, packet):
    expected = {'profile_source': 'sources', 'profile_claim': 'claims', 'profile_claim_source': 'links'}
    for table, field in expected.items():
        require(after[table] - before[table] == report[field], 'COUNT_MISMATCH_' + table.upper())
    companies = packet['companies']
    require(report['sources'] == len(companies) and report['claims'] == sum(len(c['claims']) for c in companies), 'PACKET_NOT_FULLY_WRITTEN')
    require(after['batch_sources'] == report['sources'] and after['batch_claims'] == report['claims'], 'BATCH_COUNT_MISMATCH')


def undo(conn, packet):
    """Removes only this batch: links of batch claims, batch claims, then batch sources."""
    validate(packet)
    state = counts(conn)
    require(state['batch_sources'] <= len(packet['companies']), 'MORE_THAN_THE_BATCH')
    conn.execute("""DELETE FROM profile_claim_source WHERE claim_id IN
                    (SELECT id FROM profile_claim WHERE claim_ref LIKE %s AND company_id LIKE %s)""", (CLAIM_PREFIX + '%', ID_PREFIX + '%'))
    conn.execute('DELETE FROM profile_claim WHERE claim_ref LIKE %s AND company_id LIKE %s', (CLAIM_PREFIX + '%', ID_PREFIX + '%'))
    # A batch source still linked from another claim makes this fail on the foreign key, which aborts the transaction.
    conn.execute('DELETE FROM profile_source WHERE source_ref=%s AND company_id LIKE %s', (SOURCE_REF, ID_PREFIX + '%'))
    after = counts(conn)
    require(after['batch_sources'] == 0 and after['batch_claims'] == 0, 'UNDO_INCOMPLETE')
    return {'removed_sources': state['batch_sources'], 'removed_claims': state['batch_claims']}
