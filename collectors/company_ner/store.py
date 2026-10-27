"""Short PostgreSQL transactions; never retry an unknown commit or inference."""
from datetime import datetime
import uuid

from .contracts import CONTRACT, CONTRACT_HASH, LINK_VERSION, encode, sha, job_key, source, predictions


class DatabaseUnknown(RuntimeError):
    pass


class Store:
    def __init__(self, factory):
        self.factory = factory

    def tx(self, operation, *, readonly=False):
        conn = self.factory()
        committing = False
        try:
            if conn.autocommit:
                raise ValueError('TRANSACTION_REQUIRED')
            if readonly:
                conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL statement_timeout='15s'")
            conn.execute("SET LOCAL lock_timeout='5s'")
            value = operation(conn)
            if readonly:
                conn.rollback()
            else:
                committing = True
                conn.commit()
            return value
        except Exception:
            if committing:
                raise DatabaseUnknown('NER_COMMIT_UNKNOWN_READ_STATE_NO_RETRY') from None
            raise
        finally:
            conn.close()

    def pending_sources(self, limit):
        return self.tx(lambda c: c.execute("""SELECT i.id::text item_id,i.title,i.url,
          i.published_at::text published_at,i.updated_at::text source_updated_at,
          i.raw_data->'content_capture' content_capture
          FROM intelligence.items i
          WHERE i.item_type='news' AND i.raw_data->'content_capture' IS NOT NULL
          AND NOT EXISTS(SELECT 1 FROM exp009.company_ner_inputs n WHERE n.item_id=i.id
            AND n.source_updated_at=i.updated_at AND n.contract_hash=%s)
          ORDER BY i.updated_at,i.id LIMIT %s""", (CONTRACT_HASH, limit)).fetchall(), readonly=True)

    def enqueue(self, row):
        # Invalid bodies get a durable intake hold, so a poison item cannot starve
        # later rows. No raw HTML or rejected payload is copied into this table.
        reason = None
        try:
            doc = source(row)
        except ValueError as exc:
            reason = str(exc)
            if reason not in {'SOURCE_TITLE','SOURCE_URL','BODY_NOT_ELIGIBLE','BODY_SIZE','BODY_HASH'}:
                raise
            doc = {'id': 'intelligence.items:' + str(row['item_id']), 'public_release': False}
        revision = row['source_updated_at']
        if not isinstance(revision, str) or datetime.fromisoformat(revision).tzinfo is None:
            raise ValueError('SOURCE_REVISION')
        ident = str(row['item_id'])
        if not ident.isdecimal() or int(ident) < 1:
            raise ValueError('ITEM_ID')
        key = job_key(doc['text']) if reason is None else None
        snapshot = {k:v for k,v in doc.items() if k != 'text'}
        input_key = sha(encode({'item_id': ident, 'revision': revision,
                               'contract_hash': CONTRACT_HASH, 'document': snapshot, 'reason': reason}))
        def write(c):
            if key:
                c.execute("""INSERT INTO exp009.company_ner_cache(job_key,contract_hash,contract,text_sha256,body)
                  VALUES(%s,%s,%s::jsonb,%s,%s) ON CONFLICT DO NOTHING""",
                  (key, CONTRACT_HASH, encode(CONTRACT), sha(doc['text']), doc['text']))
                actual = c.execute('SELECT body,contract FROM exp009.company_ner_cache WHERE job_key=%s', (key,)).fetchone()
                if actual is None or actual['body'] != doc['text'] or actual['contract'] != CONTRACT:
                    raise ValueError('CACHE_IDENTITY_CONFLICT')
            c.execute("""INSERT INTO exp009.company_ner_inputs(input_key,item_id,source_updated_at,
              contract_hash,job_key,document,intake_reason) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s)
              ON CONFLICT DO NOTHING""", (input_key,int(ident),revision,CONTRACT_HASH,key,encode(snapshot),reason))
            actual = c.execute("""SELECT input_key FROM exp009.company_ner_inputs
              WHERE item_id=%s AND source_updated_at=%s AND contract_hash=%s""", (int(ident),revision,CONTRACT_HASH)).fetchone()
            if actual['input_key'] != input_key:
                raise ValueError('INPUT_REVISION_CONFLICT')
            return {'input_key': input_key, 'job_key': key, 'intake_reason': reason}
        return self.tx(write)

    def claim(self):
        def write(c):
            row = c.execute("""SELECT job_key,body FROM exp009.company_ner_cache
              WHERE state='pending' AND contract_hash=%s
              ORDER BY created_at,job_key FOR UPDATE SKIP LOCKED LIMIT 1""", (CONTRACT_HASH,)).fetchone()
            if row is None:
                return None
            token = str(uuid.uuid4())
            c.execute("""UPDATE exp009.company_ner_cache SET state='running',token=%s,started_at=clock_timestamp()
              WHERE job_key=%s""", (token,row['job_key']))
            return {**row, 'token': token}
        return self.tx(write)

    def finish(self, job, answer):
        clean = predictions(job['body'], answer)
        def write(c):
            row = c.execute("""UPDATE exp009.company_ner_cache SET state='complete',result=%s::jsonb,
              finished_at=clock_timestamp() WHERE job_key=%s AND token=%s AND state='running'
              RETURNING job_key""", (encode(clean),job['job_key'],job['token'])).fetchone()
            if row is None:
                raise ValueError('STALE_NER_CLAIM')
        self.tx(write)

    def hold(self, job, reason):
        from .native import SAFE_FAILURES
        if reason not in {'INFERENCE_FAILED','OUTPUT_REJECTED'} | SAFE_FAILURES:
            raise ValueError('SAFE_REASON_REQUIRED')
        self.tx(lambda c: c.execute("""UPDATE exp009.company_ner_cache SET state='hold',hold_reason=%s,
          finished_at=clock_timestamp() WHERE job_key=%s AND token=%s AND state='running'""",
          (reason,job['job_key'],job['token'])))

    def hold_abandoned(self):
        # Longest single inference is 180s. Never requeue a dispatched computation.
        return self.tx(lambda c: c.execute("""UPDATE exp009.company_ner_cache SET state='hold',
          hold_reason='ABANDONED_UNKNOWN',finished_at=clock_timestamp()
          WHERE state='running' AND started_at<clock_timestamp()-interval '10 minutes'
          RETURNING job_key""").fetchall())

    def catalog(self):
        rows = self.tx(lambda c: c.execute("""SELECT c.id,c.name,c.country,c.status,c.notion_page_id,c.updated_at::text,
          coalesce((SELECT jsonb_agg(DISTINCT a.alias ORDER BY a.alias)
            FROM companies.company_alias a WHERE a.company_id=c.id),'[]'::jsonb) aliases
          FROM companies.company c ORDER BY c.id LIMIT 5001""").fetchall(), readonly=True)
        from .catalog import project_catalog
        return project_catalog(rows)

    def to_link(self, catalog_hash, limit):
        return self.tx(lambda c: c.execute("""SELECT n.input_key,n.document,j.body,j.result
          FROM exp009.company_ner_inputs n JOIN exp009.company_ner_cache j ON j.job_key=n.job_key
          WHERE j.state='complete' AND n.contract_hash=%s AND NOT EXISTS(
            SELECT 1 FROM exp009.company_ner_links l WHERE l.input_key=n.input_key
            AND l.catalog_hash=%s AND l.linker_version=%s)
          ORDER BY n.created_at,n.input_key LIMIT %s""", (CONTRACT_HASH,catalog_hash,LINK_VERSION,limit)).fetchall(), readonly=True)

    def save_links(self, input_key, catalog_hash, packet):
        if packet.get('public_release') is not False or packet.get('confirmed_links') != []:
            raise ValueError('CANDIDATES_ONLY')
        for mention in packet['mentions']:
            if mention['identity_confirmed'] or mention['company_id'] is not None:
                raise ValueError('NAME_ONLY_IDENTITY')
        def write(c):
            c.execute("""INSERT INTO exp009.company_ner_links(input_key,catalog_hash,linker_version,packet)
              VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""", (input_key,catalog_hash,LINK_VERSION,encode(packet)))
            old = c.execute("""SELECT packet FROM exp009.company_ner_links
              WHERE input_key=%s AND catalog_hash=%s AND linker_version=%s""", (input_key,catalog_hash,LINK_VERSION)).fetchone()
            if old['packet'] != packet:
                raise ValueError('LINK_VERSION_CONFLICT')
        self.tx(write)
