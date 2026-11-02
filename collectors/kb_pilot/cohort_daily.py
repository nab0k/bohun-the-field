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

from . import cohort, cohort_runner as runner, embed, pilot, extract


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
    }


def execute_private(directory, *, evidence_path=None, **kwargs):
    try:
        return _execute_private(directory, **kwargs)
    finally:
        if evidence_path is not None:
            runner.private_json(evidence_path,metadata_only(directory))

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    # Inputs are reviewed in workflow_dispatch; CLI flag alone grants no authority.
    if (not args.apply or os.environ.get('GITHUB_EVENT_NAME')!='workflow_dispatch'
            or os.environ.get('A11_MANUAL_COHORT')!='true'):
        parser.error('manual opt-in and --apply are required')
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
                            evidence_path=Path(os.environ['RUNNER_TEMP'])/'kb-daily'/'cohort-proof.json')
                except Exception:
                    pass  # Unknown outcomes are not retried or announced as success.
    finally:
        os.umask(old_umask)
    print('A11_COHORT '+json.dumps(result,sort_keys=True))
    # Quality cannot pass without a reviewed private delivery channel. Do not upload raw data.
    return rc


if __name__=='__main__':
    raise SystemExit(main())
