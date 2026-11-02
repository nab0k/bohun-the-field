"""Bounded CLI adapter. No default writes; files stay private.

Commands print aggregates only. Files contain private snapshots/IDs and must not
be uploaded as public artifacts. Owner approval is external to CLI flags.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from . import cohort


SNAPSHOT_SQL = """SELECT jsonb_build_object(
 'item_id',i.id,'source_id',i.source_id,'source_name',s.name,'item_type',i.item_type,
 'title',coalesce(i.title,''),'url',i.url,'published_at',i.published_at,
 'raw',jsonb_build_object('content_capture',i.raw_data->'content_capture',
                         'publisher_capture',i.raw_data->'publisher_capture',
                         '_raw_object',jsonb_typeof(i.raw_data)='object'),
 'provider_present',i.raw_data ? 'content_capture','publisher_present',i.raw_data ? 'publisher_capture',
 'embedded',e.item_id IS NOT NULL,'embedding_hash',e.text_sha256,
 'origin_item_id',e.origin_item_id,'origin_method',e.origin_method,
 'processed',EXISTS(SELECT 1 FROM knowledge.item_processing p WHERE p.item_id=i.id))
 FROM intelligence.items i JOIN intelligence.sources s ON s.id=i.source_id
 LEFT JOIN knowledge.item_embedding e ON e.item_id=i.id
 WHERE i.id=ANY(%s) ORDER BY i.id"""

SELECT_IDS_SQL = """SELECT i.id FROM intelligence.items i
 JOIN intelligence.sources s ON s.id=i.source_id
 WHERE s.name='Inoreader BOHUN' AND i.item_type='news' AND jsonb_typeof(i.raw_data)='object'
   AND i.published_at >= %s::timestamptz AND i.published_at < %s::timestamptz
   AND length(coalesce(i.raw_data->'content_capture'->>'text',''))<300
   AND coalesce(i.raw_data->'publisher_capture','null'::jsonb)='null'::jsonb
   AND i.url LIKE 'https://%%'
   AND NOT EXISTS(SELECT 1 FROM knowledge.item_embedding e WHERE e.item_id=i.id)
   AND NOT EXISTS(SELECT 1 FROM knowledge.item_processing p WHERE p.item_id=i.id)
 ORDER BY i.published_at,i.id LIMIT 12"""


def private_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.a11-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,indent=2,allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,path)  # tempfile mode0600 survives atomic replacement.
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def snapshot(conn, ids, *, for_verification=False):
    if not 1<=len(ids)<=12 or any(type(i) is not int for i in ids) or len(set(ids))!=len(ids):
        raise cohort.Stop('ID_SCOPE_INVALID')
    with conn.cursor() as cur:
        cur.execute(SNAPSHOT_SQL,(list(ids),))
        rows = [r[0] for r in cur.fetchall()]
    if len({r['item_id'] for r in rows})!=len(rows):
        raise cohort.Stop('DUPLICATE_SNAPSHOT_ID')
    if for_verification:
        for row in rows:
            row['published_at']=datetime.fromisoformat(row['published_at'].replace('Z','+00:00'))
    return {row['item_id']:row for row in rows}


def readonly(conn):
    with conn.cursor() as cur:
        cur.execute('SET TRANSACTION READ ONLY')
        cur.execute("SET LOCAL statement_timeout='15s'")


def current_sha():
    return os.environ.get('GITHUB_SHA') or subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()


def main(argv=None, *, connect=None, code_sha=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['select','write','verify'])
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--expected-manifest-sha')
    parser.add_argument('--from-utc'); parser.add_argument('--to-utc')
    parser.add_argument('--runtime-receipts')
    parser.add_argument('--manual-reviewed-ids')
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    sha=code_sha or current_sha()
    if args.mode=='select':
        try:
            a,b=(datetime.fromisoformat(s.replace('Z','+00:00')) for s in (args.from_utc,args.to_utc))
            if a.utcoffset() is None or b.utcoffset() is None or not 0<(b-a).total_seconds()<=172800:
                raise ValueError()
        except (ValueError,TypeError,AttributeError):
            parser.error('select needs an aware UTC window of at most48hours')
    else:
        if not args.expected_manifest_sha:
            parser.error('expected manifest hash is required')
        manifest=json.loads(Path(args.manifest).read_text())
        cohort.validate(manifest,args.expected_manifest_sha)
        if manifest['base_sha']!=sha:
            raise cohort.Stop('CODE_SHA_CHANGED')
        if args.mode=='write' and not args.apply:
            parser.error('write requires separate approval and --apply')
    if connect is None:
        from collectors.synthesis.live import connect
    with connect() as conn:
        if args.mode!='write': readonly(conn)
        if args.mode=='select':
            with conn.cursor() as cur:
                cur.execute(SELECT_IDS_SQL,(args.from_utc,args.to_utc))
                ids=[r[0] for r in cur.fetchall()]
            if not ids:
                private_json(args.report,{'status':'INCONCLUSIVE','reason':'NO_NEW_UNINDEXED_ITEMS','selected':0})
                conn.rollback()
                print('A11_COHORT selected=0 status=INCONCLUSIVE')
                return 2
            rows=list(snapshot(conn,ids).values()); conn.rollback()
            manifest=cohort.freeze(rows,sha)
            private_json(args.manifest,manifest)
            private_json(args.report,{'status':'SELECTED_NOT_EXECUTED','selected':len(rows),'manifest_sha256':cohort.digest(manifest)})
            print('A11_COHORT selected=%d write=0'%len(rows)); return 0
        ids=[r['item_id'] for r in manifest['rows']]
        if args.mode=='write':
            current=snapshot(conn,ids)
            # One attempt per frozen manifest. A crash/unknown outcome needs
            # read-back and a new explicit decision, never a silent rerun.
            marker=Path(args.manifest).parent / (args.expected_manifest_sha+'.attempt')
            try:
                fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            except FileExistsError:
                raise cohort.Stop('MANIFEST_ATTEMPT_ALREADY_STARTED') from None
            with os.fdopen(fd,'w') as stream:
                stream.write(args.expected_manifest_sha); stream.flush(); os.fsync(stream.fileno())
            result=cohort.execute(manifest,args.expected_manifest_sha,conn,current,
                                 fetch=cohort.fulltext.transport.fetch,extractor=cohort.fulltext.extract_text,
                                 pace=cohort.fulltext.Pacer(),clock=time.monotonic)
            private_json(args.report,result)
            print('A11_COHORT attempted=%d written=%d stop=%s'%(len(result['receipts']),
                  sum(r['reason']=='WRITTEN' for r in result['receipts']),result['stop']))
            if result['stop']: return 1
            return 0 if len(result['receipts'])==len(ids) and all(r['reason']=='WRITTEN' for r in result['receipts']) else 2
        current=snapshot(conn,ids,for_verification=True); conn.rollback()
        result=json.loads(Path(args.report).read_text())
        receipts=json.loads(Path(args.runtime_receipts).read_text()) if args.runtime_receipts else []
        manual=json.loads(Path(args.manual_reviewed_ids).read_text()) if args.manual_reviewed_ids else []
        proof=cohort.verify(manifest,args.expected_manifest_sha,result,current,
                            runtime_receipts=receipts,manual_reviewed_ids=manual)
        private_json(str(args.report)+'.verified.json',proof)
        print('A11_COHORT status=%s stored=%d e2e=%d'%(proof['status'],proof['stored'],proof['end_to_end_proven']))
        return 0 if proof['status']=='SUCCEEDED' else 2


if __name__=='__main__':
    try: raise SystemExit(main())
    except Exception:
        raise SystemExit('A11_COHORT_FAILED_NO_RETRY') from None
