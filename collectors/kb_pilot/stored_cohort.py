"""A new admission manifest for the exact stored subset of A11 run41439209286.

Never reconstruct the deleted original manifest or pretend this was a new fetch.
Evidence includes hashes only; raw snapshots remain private runtime files.
"""
import copy
from datetime import datetime
import json
from pathlib import Path
import re

from . import cohort, body_selection as body

SCHEMA='a11-stored-cohort-v1'
IDS=(206937,207830,207831,207832,207834,207835,207836,207838,207839,207840)
PARENT_RUN=41439209286
EVIDENCE=Path(__file__).with_name('stored_cohort_evidence_v1.json')


def evidence():
    data=json.loads(EVIDENCE.read_text())
    receipts=data.get('receipts',[])
    if (data.get('schema')!='a11-stored-evidence-v1' or data.get('parent_run')!=PARENT_RUN
            or len(receipts)!=len(IDS) or {r['item_id'] for r in receipts}!=set(IDS)):
        raise cohort.Stop('STORED_EVIDENCE_INVALID')
    for r in receipts:
        for key in ('provider_before_sha256','publisher_capture_sha256','publisher_text_sha256'):
            if not re.fullmatch('[0-9a-f]{64}',r.get(key) or ''):
                raise cohort.Stop('STORED_EVIDENCE_INVALID')
    return data


def checked_rows(rows, data):
    if (not isinstance(rows,list) or len(rows)!=len(IDS)
            or any(type(r.get('item_id')) is not int for r in rows)
            or {r['item_id'] for r in rows}!=set(IDS)):
        raise cohort.Stop('STORED_ID_SCOPE_CHANGED')
    expected={r['item_id']:r for r in data['receipts']}
    for r in rows:
        raw=r.get('raw'); cap=raw.get('publisher_capture') if isinstance(raw,dict) else None
        if (r.get('source_name')!='Inoreader BOHUN' or r.get('item_type')!='news'
                or type(r.get('source_id')) is not int or r['source_id']<=0
                or r.get('embedded') is not False or r.get('processed') is not False
                or any(r.get(k) is not None for k in ('embedding_hash','origin_item_id','origin_method'))
                or not isinstance(raw,dict) or raw.get('_raw_object') is not True
                or type(r.get('provider_present')) is not bool or r.get('publisher_present') is not True
                or not isinstance(r.get('title'),str)):
            raise cohort.Stop('STORED_STATE_NOT_ELIGIBLE')
        try:
            cohort.fulltext.transport.target(r['url'])
            pub=datetime.fromisoformat(r['published_at'].replace('Z','+00:00'))
            if pub.utcoffset() is None: raise ValueError()
        except (ValueError,TypeError,KeyError,AttributeError):
            raise cohort.Stop('STORED_IDENTITY_INVALID') from None
        receipt=expected[r['item_id']]
        selected=body.select_body(raw,r['url'])
        if (cohort.digest(raw.get('content_capture'))!=receipt['provider_before_sha256']
                or cohort.digest(cap)!=receipt['publisher_capture_sha256']
                or selected.source_kind!='publisher_article'
                or selected.text_sha256!=receipt['publisher_text_sha256']):
            raise cohort.Stop('STORED_BODY_CHANGED')


def freeze(rows, code_sha):
    if not re.fullmatch('[0-9a-f]{40}',code_sha or ''):
        raise cohort.Stop('CODE_SHA_CHANGED')
    data=evidence();checked_rows(rows,data)
    rows=sorted(copy.deepcopy(rows),key=lambda r:r['item_id'])
    return {'schema':SCHEMA,'base_sha':code_sha,'parent_run':PARENT_RUN,
            'evidence_sha256':cohort.digest(data),'rows':rows,'snapshot_sha256':cohort.digest(rows)}


def validate(manifest, expected_digest):
    if manifest.get('schema')!=SCHEMA or cohort.digest(manifest)!=expected_digest:
        raise cohort.Stop('MANIFEST_CHANGED')
    check=freeze(manifest.get('rows'),manifest.get('base_sha'))
    if check!=manifest:
        raise cohort.Stop('STORED_MANIFEST_CHANGED')


def revalidate(manifest, expected_digest, current):
    validate(manifest,expected_digest)
    if set(current)!=set(IDS):raise cohort.Stop('STORED_ID_SCOPE_CHANGED')
    fresh=[current[i] for i in IDS]
    checked_rows(fresh,evidence())
    if cohort.digest(fresh)!=manifest['snapshot_sha256']:
        raise cohort.Stop('STORED_SNAPSHOT_CHANGED')


def admission_receipt(manifest, expected_digest):
    validate(manifest,expected_digest)
    data=evidence()
    return {'manifest_sha256':expected_digest,'base_sha':manifest['base_sha'],
            'parent_run':PARENT_RUN,'http_attempts':0,'stop':None,'not_attempted_ids':[],
            'receipts':[dict(r,reason='STORED_ADMITTED') for r in data['receipts']]}
