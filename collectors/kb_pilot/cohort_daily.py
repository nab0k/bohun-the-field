"""One manual cohort; ordinary cron never calls this module.

Raw snapshots, model output and receipts stay in a private temporary directory.
Only counts and allowlisted ID/hash audit metadata leave this runner. A verified snapshot never implies manual quality.
"""
import argparse
from contextlib import ExitStack, contextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from . import cohort, cohort_runner as runner, embed, pilot, extract, stored_cohort


@contextmanager
def scoped_readers(manifest, manifest_sha, run_sha, receipts, expected_bodies=None):
    """Observe real reader invocations and constrain SQL before ORDER/LIMIT.

    Scoped only for this process. Restore every reader even on an unknown result.
    """
    cohort.validate(manifest, manifest_sha)
    if run_sha != manifest['base_sha']:
        raise cohort.Stop('CODE_SHA_CHANGED')
    ids = cohort.checked_ids([r['item_id'] for r in manifest['rows']])
    with ExitStack() as stack:
        for module, name, consumer in ((embed,'pending','embed'), (pilot,'load_items','pilot'),
                                       (extract,'pending','extract')):
            original = getattr(module, name)
            def bounded(*args, _reader=original, **kwargs):
                if 'item_ids' in kwargs:
                    raise cohort.Stop('NESTED_SCOPE')
                result = _reader(*args, item_ids=ids, **kwargs)
                for row in result:
                    if row.get('item_id') not in ids:
                        raise cohort.Stop('READER_ID_OUTSIDE_COHORT')
                    if expected_bodies is not None and (
                            row.get('body_source_kind') != 'publisher_article'
                            or row.get('body_text_sha256') != expected_bodies.get(row['item_id'])):
                        raise cohort.Stop('READER_BODY_CHANGED')
                return result
            stack.enter_context(patch.object(module, name, cohort.observe_reader(
                bounded, consumer, manifest, manifest_sha, run_sha, receipts)))
        original_run = embed.run
        def embed_new(*args, **kwargs):
            return original_run(*args, new_only=True, **kwargs)
        stack.enter_context(patch.object(embed, 'run', embed_new))
        yield


def storage_gate(manifest, manifest_sha, report, current):
    proof = cohort.verify(manifest, manifest_sha, report, current)
    if proof['stored'] != len(manifest['rows']) or report.get('stop'):
        raise cohort.Stop('INCOMPLETE_STORAGE_NO_MODELS')
    return proof


def _execute_private(directory, *, code_sha, now, command=runner.main, connect=None,
                    embedding=embed.main, synthesis=pilot.main, extraction=extract.main):
    """External boundaries are injectable; no retries, replacements or admission.

    The pilot keeps HOLD candidates; ordinary daily admission remains untouched.
    Extraction retains its existing free-only rule; pilot keeps existing cap.
    """
    directory = Path(directory)
    m = directory/'manifest.json'; report = directory/'capture.json'
    day_to = now.date(); day_from = day_to - timedelta(days=1)
    common = ['--manifest',str(m),'--report',str(report)]
    end = datetime.combine(day_to+timedelta(days=1),datetime.min.time(),tzinfo=timezone.utc)
    start = end-timedelta(days=2)
    inject = {'code_sha':code_sha}
    if connect is not None: inject['connect']=connect
    rc = command(['select',*common,'--from-utc',start.isoformat(),'--to-utc',end.isoformat()],**inject)
    if rc: return {'status':'INCONCLUSIVE','reason':'NO_SELECTION','selected':0}, rc
    manifest = json.loads(m.read_text()); h = cohort.digest(manifest)
    cohort.validate(manifest,h)
    if manifest['base_sha'] != code_sha: raise cohort.Stop('CODE_SHA_CHANGED')
    runner.private_json(directory/'stage.json',{'stage':'CAPTURE'})
    rc = command(['write',*common,'--expected-manifest-sha',h,'--apply'],**inject)
    if rc: return {'status':'INCONCLUSIVE','reason':'CAPTURE_INCOMPLETE','selected':len(manifest['rows'])}, rc
    if connect is None:
        from collectors.synthesis.live import connect
    def read_current():
        with connect() as conn:
            runner.readonly(conn)
            current = runner.snapshot(conn,[r['item_id'] for r in manifest['rows']],for_verification=True)
            conn.rollback()
            return current
    captured=json.loads(report.read_text())
    storage_gate(manifest,h,captured,read_current())
    expected={r['item_id']:r['publisher_text_sha256'] for r in captured['receipts']}
    receipts=[]
    try:
        with scoped_readers(manifest,h,code_sha,receipts,expected):
            # Prior cron environment is restored after this isolated invocation.
            with patch.dict(os.environ,{'LIMIT':'12','DAY_FROM':day_from.isoformat(),
                                        'KB_EMBED_BACKEND':'cloudflare'}):
                if embedding(): raise cohort.Stop('EMBEDDING_FAILED_NO_RETRY')
        # Snapshot replay is outside observers: it cannot forge runtime receipts.
        indexed=cohort.verify(manifest,h,captured,read_current())
        if not all(p['storage_ok'] and p['embedding_ok'] and p['origin_ok'] for p in indexed['proofs']):
            raise cohort.Stop('INDEX_READBACK_FAILED_NO_MODELS')
        with scoped_readers(manifest,h,code_sha,receipts,expected):
            args=['--day-from',day_from.isoformat(),'--day-to',day_to.isoformat(),'--limit','12']
            if synthesis([*args,'--source','items','--new-only','--publish-hold','--batch',
                          '--out',str(directory/'pilot')]):
                raise cohort.Stop('PILOT_FAILED_NO_RETRY')
            if extraction(args): raise cohort.Stop('EXTRACTION_FAILED_NO_RETRY')
    finally:
        runner.private_json(directory/'reader-receipts.json',receipts)
    command(['verify',*common,'--expected-manifest-sha',h,
             '--runtime-receipts',str(directory/'reader-receipts.json')],**inject)
    proof=json.loads(Path(str(report)+'.verified.json').read_text())
    return {k:proof[k] for k in ('status','stored','end_to_end_proven','manual_reviewed')}, 2




def _resume_private(directory, *, code_sha, now=None, connect=None,
                    embedding=embed.main, synthesis=pilot.main, extraction=extract.main):
    """No select/write/fetch commands; admit the known stored subset only."""
    directory=Path(directory)
    if connect is None:
        from collectors.synthesis.live import connect
    def read_current(verification=False):
        with connect() as conn:
            runner.readonly(conn)
            current=runner.snapshot(conn,list(stored_cohort.IDS),for_verification=verification)
            conn.rollback()
            return current
    current=read_current()
    manifest=stored_cohort.freeze(list(current.values()),code_sha);h=cohort.digest(manifest)
    runner.private_json(directory/'manifest.json',manifest)
    report=stored_cohort.admission_receipt(manifest,h)
    runner.private_json(directory/'capture.json',report)
    stored_cohort.revalidate(manifest,h,read_current())
    try:
        with (directory/'resume.attempt').open('x') as marker:
            marker.write(h)
    except FileExistsError:
        raise cohort.Stop('RESUME_ATTEMPT_ALREADY_STARTED') from None
    expected={r['item_id']:r['publisher_text_sha256'] for r in report['receipts']}
    dates=[r['published_at'][:10] for r in manifest['rows']]
    receipts=[]
    try:
        runner.private_json(directory/'stage.json',{'stage':'EMBED'})
        with scoped_readers(manifest,h,code_sha,receipts,expected):
            with patch.dict(os.environ,{'LIMIT':'10','DAY_FROM':min(dates),'KB_EMBED_BACKEND':'cloudflare'}):
                if embedding():raise cohort.Stop('EMBEDDING_FAILED_NO_RETRY')
        # Replays outside observers cannot create runtime input evidence.
        runner.private_json(directory/'stage.json',{'stage':'INDEX_READBACK'})
        indexed=cohort.verify(manifest,h,report,read_current(True))
        if not all(x['storage_ok'] and x['embedding_ok'] and x['origin_ok'] for x in indexed['proofs']):
            raise cohort.Stop('INDEX_READBACK_FAILED_NO_MODELS')
        args=['--day-from',min(dates),'--day-to',max(dates),'--limit','10']
        with scoped_readers(manifest,h,code_sha,receipts,expected):
            runner.private_json(directory/'stage.json',{'stage':'PILOT'})
            if synthesis([*args,'--source','items','--new-only','--publish-hold','--batch',
                          '--out',str(directory/'pilot')]):raise cohort.Stop('PILOT_FAILED_NO_RETRY')
            runner.private_json(directory/'stage.json',{'stage':'EXTRACT'})
            if extraction(args):raise cohort.Stop('EXTRACTION_FAILED_NO_RETRY')
    finally:
        runner.private_json(directory/'reader-receipts.json',receipts)
    runner.private_json(directory/'stage.json',{'stage':'FINAL_READBACK'})
    proof=cohort.verify(manifest,h,report,read_current(True),runtime_receipts=receipts)
    runner.private_json(directory/'capture.json.verified.json',proof)
    return {k:proof[k] for k in ('status','stored','end_to_end_proven','manual_reviewed')},2


SAFE_REASONS=frozenset(('NO_SELECTION','CAPTURE_INCOMPLETE','STORED_EVIDENCE_INVALID',
    'STORED_ID_SCOPE_CHANGED','STORED_STATE_NOT_ELIGIBLE','STORED_IDENTITY_INVALID','STORED_BODY_CHANGED',
    'RESUME_ATTEMPT_ALREADY_STARTED','MANIFEST_CHANGED','STORED_MANIFEST_CHANGED','STORED_SNAPSHOT_CHANGED','CODE_SHA_CHANGED',
    'EMBEDDING_FAILED_NO_RETRY','INDEX_READBACK_FAILED_NO_MODELS','PILOT_FAILED_NO_RETRY',
    'EXTRACTION_FAILED_NO_RETRY','READER_ID_OUTSIDE_COHORT','READER_BODY_CHANGED','INCOMPLETE_STORAGE_NO_MODELS'))
SAFE_STAGES=frozenset(('ADMISSION','CAPTURE','EMBED','INDEX_READBACK','PILOT','EXTRACT','FINAL_READBACK'))
CAPTURE_REASONS=frozenset(('WRITTEN','STORED_ADMITTED','STALE_NOT_WRITTEN','PUBLISHER_WRITE_FAILED',
    'robots_denied','robots_fetch_failed','robots_http_301','robots_http_302',
    'robots_http_403','robots_http_429','page_receipt_mismatch','extract_failed','extract_shape_invalid',
    'text_utf8_invalid','text_bytes_exceeded','text_too_short','not_longer_than_provider',
    'run_deadline','request_limit','http_status_403','http_status_429','fetch_failed'))

# A finite vocabulary; no URL, text, arbitrary exception or account identifiers.
_TRANSPORT_CODES=frozenset(('url_invalid','ip_literal_denied','tls_configuration','deadline','nonpublic_dns',
    'peer_mismatch','content_encoding','framing_invalid','transfer_encoding_unsupported','bytes_exceeded',
    'content_type','charset_invalid','body_truncated','fetch_failed_no_retry','url_not_allowlisted',
    'limit_invalid','timeout_invalid','worker_failed','ipc_bytes_exceeded','ipc_invalid','child_cleanup_failed',
    'utf8_invalid')) | frozenset('http_'+str(i) for i in range(100,600))
CAPTURE_REASONS=CAPTURE_REASONS | _TRANSPORT_CODES | frozenset('robots_'+x for x in _TRANSPORT_CODES)


def enum_or_unknown(value, allowed):
    return value if isinstance(value,str) and value in allowed else 'UNKNOWN'

def metadata_only(directory):
    """Allowlist audit fields, never provider JSON/title/URL/text/model output."""
    directory=Path(directory)
    def load(name, default):
        path=directory/name
        return json.loads(path.read_text()) if path.exists() else default
    manifest=load('manifest.json',{})
    report=load('capture.json',{})
    proof=load('capture.json.verified.json',{})
    receipts=load('reader-receipts.json',[])
    return {
        'schema':'a11-cohort-audit-v1',
        'base_sha':manifest.get('base_sha'),
        'manifest_sha256':cohort.digest(manifest) if manifest else None,
        'item_ids':[r['item_id'] for r in manifest.get('rows',[])],
        'capture_receipts':[{k:r.get(k) for k in ('item_id','provider_before_sha256',
            'publisher_capture_sha256','publisher_text_sha256')} for r in report.get('receipts',[])],
        'reader_receipts':[{k:r.get(k) for k in ('item_id','consumer','source_kind','body_sha256',
            'payload_sha256','run_sha','manifest_sha256','observed_at_call')} for r in receipts],
        'proofs':[{k:r.get(k) for k in ('item_id','storage_ok','embedding_ok','origin_ok',
            'reader_replay_ok','runtime_input_proven')} for r in proof.get('proofs',[])],
        'status':proof.get('status','INCONCLUSIVE'),
        'manual_reviewed':0,
        'quality_phase':'NOT_RUN_NO_PRIVATE_TEXT_DELIVERY',
        'stage':enum_or_unknown(load('stage.json',{}).get('stage'),SAFE_STAGES),
        'stop':enum_or_unknown(load('diagnostic.json',{}).get('reason'),SAFE_REASONS),
        'http_attempts':report.get('http_attempts') if type(report.get('http_attempts')) is int else None,
        'capture_outcomes':[{'item_id':r.get('item_id'),'reason':enum_or_unknown(r.get('reason'),CAPTURE_REASONS)}
                            for r in report.get('receipts',[])],
    }


def execute_private(directory, *, evidence_path=None, resume=False, **kwargs):
    runner.private_json(Path(directory)/'stage.json',{'stage':'ADMISSION'})
    try:
        result=(_resume_private if resume else _execute_private)(directory, **kwargs)
        if result[0].get('reason'):
            runner.private_json(Path(directory)/'diagnostic.json',
                                {'reason':enum_or_unknown(result[0]['reason'],SAFE_REASONS)})
        return result
    except Exception as exc:
        runner.private_json(Path(directory)/'diagnostic.json',
                            {'reason':enum_or_unknown(str(exc),SAFE_REASONS)})
        raise
    finally:
        if evidence_path is not None:
            runner.private_json(evidence_path,metadata_only(directory))

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--resume-stored',action='store_true')
    args=parser.parse_args(argv)
    # Inputs are reviewed in workflow_dispatch; CLI flag alone grants no authority.
    if (not args.apply or os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch'
            or os.environ.get('A11_MANUAL_RESUME' if args.resume_stored else 'A11_MANUAL_COHORT')!='true'):
        parser.error('manual opt-in and --apply are required')
    if args.resume_stored and (os.environ.get('A11_MANUAL_COHORT')=='true'
            or os.environ.get('A11_EXPECTED_SHA')!=runner.current_sha()):
        parser.error('resume requires an exact expected SHA and exclusive opt-in')
    if not all(os.environ.get(k, '').strip() for k in ('CLOUDFLARE_ACCOUNT_ID','CLOUDFLARE_WORKERS_AI_TOKEN')):
        parser.error('existing Cloudflare embedding configuration is required')
    old_umask=os.umask(0o077)
    result={'status':'INCONCLUSIVE','reason':'STOPPED_NO_RETRY'}; rc=1
    try:
        with tempfile.TemporaryDirectory(prefix='a11-private-',dir=os.environ.get('RUNNER_TEMP')) as temp:
            # No raw/model data in Actions logs or in kb-daily upload path.
            with open(Path(temp)/'private.log','w',encoding='utf-8') as log:
                try:
                    with redirect_stdout(log),redirect_stderr(log):
                        result,rc=execute_private(temp,code_sha=runner.current_sha(),now=datetime.now(timezone.utc),
                            evidence_path=Path(os.environ['RUNNER_TEMP'])/'kb-daily'/'cohort-proof.json',resume=args.resume_stored)
                except Exception:
                    pass  # Unknown outcomes are not retried or announced as success.
    finally:
        os.umask(old_umask)
    print('A11_COHORT '+json.dumps(result,sort_keys=True))
    # Quality cannot pass without a reviewed private delivery channel. Do not upload raw data.
    return rc


if __name__=='__main__':
    raise SystemExit(main())
