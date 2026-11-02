"""Private, bounded A11 cohort executor and evidence checker; no network defaults.

This module is not installed in the product and has no live CLI. Caller-supplied
snapshots/transports/stores must be separately admitted before any live use.
"""
import copy
import hashlib
import json
import re

from collectors.kb_pilot import body_selection as body, fulltext, pilot, extract, embed

SCHEMA = 'a11-cohort-v1'


class Stop(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def eligible(row):
    try:
        fulltext.transport.target(row['url'])
    except (ValueError, KeyError, TypeError):
        return False
    if (row.get('item_type') != 'news' or row.get('source_name') != 'Inoreader BOHUN'
            or type(row.get('item_id')) is not int or row['item_id'] <= 0
            or type(row.get('source_id')) is not int or row['source_id'] <= 0
            or row.get('embedded') is not False or row.get('processed') is not False
            or type(row.get('provider_present')) is not bool or type(row.get('publisher_present')) is not bool
            or row.get('origin_method') not in (None, 'self') or not isinstance(row.get('raw'), dict)):
        return False
    raw = row['raw']
    if raw.get('_raw_object') is False or raw.get('publisher_capture') is not None:
        return False
    p = raw.get('content_capture')
    if p is not None and not isinstance(p, dict):
        return False
    text = p.get('text') if isinstance(p, dict) else None
    return text is None or isinstance(text, str) and len(text) < 300


def freeze(rows, sha):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 12:
        raise Stop('COHORT_MUST_HAVE_1_TO_12_ITEMS')
    if len({r.get('item_id') for r in rows}) != len(rows):
        raise Stop('DUPLICATE_ITEM_ID')
    if not re.fullmatch('[0-9a-f]{40}', sha or '') or any(not eligible(r) for r in rows):
        raise Stop('COHORT_NOT_ELIGIBLE')
    snapshot = copy.deepcopy(rows)
    return {'schema': SCHEMA, 'base_sha': sha, 'rows': snapshot, 'snapshot_sha256': digest(snapshot)}


def validate(manifest, expected_digest):
    # Digest is an integrity check, NOT a replacement for owner authorization.
    if manifest.get('schema') != SCHEMA or digest(manifest) != expected_digest:
        raise Stop('MANIFEST_CHANGED')
    frozen = freeze(manifest.get('rows'), manifest.get('base_sha'))
    if frozen['snapshot_sha256'] != manifest.get('snapshot_sha256'):
        raise Stop('SNAPSHOT_CHANGED')


def target_item(row):
    raw = row['raw']
    p = raw.get('content_capture') or {}
    text = p.get('text') or ''
    return {'item_id': row['item_id'], 'source_id': row['source_id'], 'url': row['url'],
            'raw': copy.deepcopy(raw), 'provider_chars': len(text),
            'provider_present': bool(row['provider_present']), 'publisher_present': bool(row['publisher_present']),
            'embedded': row['embedded'], 'processed': row['processed']}


def execute(manifest, expected_digest, conn, current_rows, *, fetch, extractor, pace, clock):
    """One pass only, exact IDs. All external interfaces are mandatory injections.

    No selection/replacement/refetch of failed IDs. The current product CAS writer
    remains the final guard for concurrent changes after snapshot revalidation.
    """
    validate(manifest, expected_digest)
    ids = {r['item_id'] for r in manifest['rows']}
    if set(current_rows) != ids:
        raise Stop('CURRENT_ID_SET_CHANGED')
    expected_fields = ('item_id', 'source_id', 'url', 'raw', 'provider_present', 'publisher_present',
                       'embedded', 'processed', 'source_name', 'item_type', 'origin_method', 'title', 'published_at')
    for row in manifest['rows']:
        fresh = current_rows[row['item_id']]
        if (not eligible(fresh) or any(fresh.get(k) != row.get(k) for k in expected_fields)):
            raise Stop('CURRENT_SNAPSHOT_CHANGED')
    receipts = []
    started = clock()
    requests = 0
    def guarded_fetch(*args, **kwargs):
        nonlocal requests
        left = 600 - (clock() - started)
        if left <= 0:
            raise fulltext.transport.FetchError('RUN_DEADLINE')
        if requests >= 40:
            raise fulltext.transport.FetchError('REQUEST_LIMIT')
        requests += 1
        kwargs['timeout'] = min(kwargs.get('timeout', 20), left)
        return fetch(*args, **kwargs)
    robots = fulltext.Robots(guarded_fetch, pace)
    stop = None
    for row in manifest['rows']:
        if clock() - started >= 600:
            stop = 'RUN_DEADLINE'
            break
        reason, capture = fulltext.read_one(target_item(row), robots, guarded_fetch, extractor, pace)
        receipt = {'item_id': row['item_id'], 'reason': reason,
                   'provider_before_sha256': digest(row['raw'].get('content_capture')),
                   'publisher_capture_sha256': None, 'publisher_text_sha256': None}
        if capture is not None:
            receipt['publisher_capture_sha256'] = digest(capture)
            receipt['publisher_text_sha256'] = capture['text_sha256']
            try:
                receipt['reason'] = fulltext.write_capture(conn, target_item(row), capture)
            except RuntimeError:
                receipt['reason'] = stop = 'PUBLISHER_WRITE_FAILED'
        receipts.append(receipt)
        if reason.endswith('run_deadline') or reason.endswith('request_limit'):
            stop = reason.upper()
        if stop:
            break
    return {'manifest_sha256': expected_digest, 'base_sha': manifest['base_sha'],
            'http_attempts': requests, 'receipts': receipts, 'stop': stop,
            'not_attempted_ids': sorted(ids - {r['item_id'] for r in receipts})}


class RowsCursor:
    """Read-only snapshot replay, never proof of historical model execution."""
    def __init__(self, rows): self.rows = rows
    def execute(self, *_): pass
    def fetchall(self): return self.rows


def observe_reader(reader, consumer, manifest, expected_digest, run_sha, sink):
    """Wrap an actual reader call, retaining only IDs/hash receipts in a private sink.

    Does not claim that the model finished or that the synthesis was good. The
    caller must attach this wrapper to the reviewed runner before execution.
    """
    validate(manifest, expected_digest)
    if run_sha != manifest['base_sha'] or consumer not in ('pilot','extract','embed'):
        raise Stop('OBSERVER_SCOPE_CHANGED')
    ids = {r['item_id'] for r in manifest['rows']}
    def observed(*args, **kwargs):
        result = reader(*args, **kwargs)
        for item in result:
            if item.get('item_id') not in ids:
                continue
            sink.append({'item_id': item['item_id'], 'consumer': consumer,
                         'source_kind': item.get('body_source_kind'),
                         'body_sha256': item.get('body_text_sha256'), 'run_sha': run_sha,
                         'payload_sha256': body.sha(item.get('text') or ''),
                         'manifest_sha256': expected_digest, 'observed_at_call': True})
        return result
    return observed


def verify(manifest, expected_digest, report, current_rows, *, manual_reviewed_ids=(), runtime_receipts=()):
    validate(manifest, expected_digest)
    if (report.get('manifest_sha256') != expected_digest or report.get('base_sha') != manifest['base_sha']):
        raise Stop('RUN_RECEIPT_CHANGED')
    ids = {r['item_id'] for r in manifest['rows']}
    entries = report.get('receipts') or []
    if len({r['item_id'] for r in entries}) != len(entries) or any(r['item_id'] not in ids for r in entries):
        raise Stop('UNEXPECTED_RECEIPT_ID')
    receipts = {r['item_id']: r for r in entries}
    proofs = []
    for before in manifest['rows']:
        iid = before['item_id']; after = current_rows.get(iid); receipt = receipts.get(iid, {})
        proof = {'item_id': iid, 'storage_ok': False, 'embedding_ok': False,
                 'origin_ok': False, 'reader_replay_ok': False, 'runtime_input_proven': False,
                 'reason': receipt.get('reason', 'NOT_ATTEMPTED')}
        proofs.append(proof)
        if receipt.get('reason') != 'WRITTEN':
            continue
        if (not after or any(after.get(k) != before.get(k) for k in ('item_id','source_id','url','source_name','item_type'))
                or digest(after.get('raw',{}).get('content_capture')) != digest(before['raw'].get('content_capture'))):
            proof['reason'] = 'READBACK_PROVIDER_OR_IDENTITY_CHANGED'; continue
        cap = after['raw'].get('publisher_capture')
        selected = body.select_body(after['raw'], after['url'])
        if (selected.source_kind != 'publisher_article' or digest(cap) != receipt['publisher_capture_sha256']
                or selected.text_sha256 != receipt['publisher_text_sha256']):
            proof['reason'] = 'READBACK_PUBLISHER_INVALID'; continue
        proof['storage_ok'] = True
        proof['embedding_ok'] = after.get('embedding_hash') == body.sha(embed.item_text(after['title'], selected.text))
        proof['origin_ok'] = after.get('origin_method') in ('self','same_text','near_duplicate') and bool(after.get('origin_item_id'))
        if not proof['embedding_ok'] or not proof['origin_ok']:
            proof['reason'] = 'WAITING_EMBEDDING_OR_ORIGIN'; continue
        pub = after['published_at']
        raw = after['raw']; h = after['embedding_hash']; method = after['origin_method']; title = after['title']; url = after['url']
        p = pilot.load_items(RowsCursor([(iid,title,url,pub,raw,h,method)]), pub.date().isoformat(), pub.date().isoformat())
        e = extract.pending(RowsCursor([(iid,title,url,raw,pub,after['origin_item_id'],method,h)]),pub.date().isoformat(),pub.date().isoformat(),1)
        proof['reader_replay_ok'] = all(x and x[0]['body_source_kind']=='publisher_article'
                                          and x[0]['body_text_sha256']==selected.text_sha256 for x in (p,e))
        needed = {'pilot','extract'}
        payloads = {'pilot': body.sha(p[0]['text']), 'extract': body.sha(e[0]['text'])} if p and e else {}
        seen = {r.get('consumer') for r in runtime_receipts if r.get('item_id')==iid
                and r.get('source_kind')=='publisher_article' and r.get('body_sha256')==selected.text_sha256
                and r.get('run_sha')==manifest['base_sha'] and r.get('observed_at_call') is True
                and r.get('manifest_sha256')==expected_digest
                and r.get('payload_sha256')==payloads.get(r.get('consumer'))}
        proof['runtime_input_proven'] = needed <= seen
        proof['reason'] = 'READY_SNAPSHOT_ONLY' if not proof['runtime_input_proven'] else 'RUNTIME_RECEIPT_MATCHED'
    storage = [p for p in proofs if p['storage_ok']]
    fully = [p for p in proofs if all(p[k] for k in ('storage_ok','embedding_ok','origin_ok','reader_replay_ok','runtime_input_proven'))]
    manual = set(manual_reviewed_ids) & {p['item_id'] for p in fully}
    failed = any(p['reason'].startswith('READBACK_') for p in proofs)
    # A green job with zero writes or snapshot-only replay is never end-to-end PASS.
    status = 'FAILED' if failed else 'INCONCLUSIVE' if not storage else 'PARTIAL'
    if fully and len(fully)==len(manifest['rows']) and len(manual)>=min(5,len(fully)) and report.get('stop') is None:
        status = 'SUCCEEDED'
    return {'status': status, 'snapshot_replay_only': not bool(fully), 'proofs': proofs,
            'stored': len(storage), 'end_to_end_proven': len(fully), 'manual_reviewed': len(manual)}


def checked_ids(ids):
    if (not isinstance(ids, (list, tuple)) or not 1 <= len(ids) <= 12
            or any(type(i) is not int or i <= 0 for i in ids) or len(set(ids)) != len(ids)):
        raise Stop('ID_SCOPE_INVALID')
    return list(ids)
