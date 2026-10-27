"""Fill missing provider content for an existing item; never replace stored body."""
import hashlib
import json


def eligible_capture(item):
    capture=item.get('raw_data',{}).get('content_capture')
    if not isinstance(capture,dict) or capture.get('version')!=1:
        return None
    if capture.get('status')!='BODY_PRESENT_COMPLETENESS_UNVERIFIED':
        return None
    for field in ['text','raw_html']:
        value=capture.get(field)
        if not isinstance(value,str) or not value:
            return None
        raw=value.encode('utf-8')
        cap=128*1024 if field=='text' else 2*1024*1024
        if len(raw)>cap or hashlib.sha256(raw).hexdigest()!=capture.get(field+'_sha256'):
            return None
    if len(json.dumps(capture,ensure_ascii=False).encode())>3*1024*1024:
        return None
    return capture


BACKFILL_SQL="""UPDATE intelligence.items
 SET raw_data=jsonb_set(raw_data,'{content_capture}',%s::jsonb,true),
     updated_at=clock_timestamp()
 WHERE source_id=%s AND external_id=%s AND item_type='news'
   AND url IS NOT DISTINCT FROM %s
   AND title=%s
   AND raw_data->>'inoreader_id'=%s
   AND (raw_data->'content_capture' IS NULL
        OR raw_data->'content_capture'='null'::jsonb
        OR raw_data#>>'{content_capture,status}' IN
            ('METADATA_ONLY','EMPTY_TEXT','CAPTURE_BUDGET_EXHAUSTED','TOO_LARGE','UNSUPPORTED_SHAPE'))
 RETURNING id"""


def backfill_missing_body(cursor,source_id,item,jsonb):
    capture=eligible_capture(item)
    if capture is None:return False
    cursor.execute(BACKFILL_SQL,(jsonb(capture),source_id,item['external_id'],
                   item['url'],item['title'],item['external_id']))
    return cursor.fetchone() is not None
