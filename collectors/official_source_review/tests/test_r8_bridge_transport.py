import io
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from r8_source_reader import make_window, WindowError
from r8_bridge import BridgeError, prepare_window, require_bound_receipt
from r8_transport import TransportError, bounded_get, retry_after_seconds

W = make_window('2026-10-15T09:00:00','2026-10-15T10:00:00')

def release(d='2021-01-01T12:00:00Z'):
    return {'ocid':'ocds-h6vhtk-test','id':'r001','date':d,'tag':['tender'],'tender':{'title':'Tender'}}

def fake_prepare(pages, *, verified_complete):
    assert verified_complete and len(pages)==1
    rows=pages[0]['releases']; tender=[r for r in rows if 'tender' in r.get('tag',[])]
    return {'source':'uk_fts','qa':'HOLD','public_release':False,'source_window_complete':True,
            'production_write_approved':False,'observations':len(rows),
            'eligible_unique_tender_notices':len(tender),'pilot_candidates':min(len(tender),10),
            'selection_limited':len(tender)>10,'candidates':[SimpleNamespace(published_at=datetime.fromisoformat(r['date'].replace('Z','+00:00'))) for r in tender[:10]]}

def transport(url):
    return json.dumps({'releases':[release()],'links':{}}).encode()

class Bridge(unittest.TestCase):
    def test_old_publication_date_is_accepted_in_update_window(self):
        m=prepare_window(W,transport,fake_prepare)
        require_bound_receipt(m,window_start=W.utc_from,window_end=W.utc_to)
        self.assertEqual(m['candidates'][0].published_at.year,2021)
    def test_wrong_receipt_window_rejected(self):
        m=prepare_window(W,transport,fake_prepare)
        with self.assertRaisesRegex(BridgeError,'mismatch'):
            require_bound_receipt(m,window_start=datetime(2026,10,15,9,tzinfo=timezone.utc),window_end=W.utc_to)
    def test_missing_receipt_rejected(self):
        with self.assertRaises(BridgeError): require_bound_receipt({},window_start=W.utc_from,window_end=W.utc_to)
    def test_malformed_empty_next_is_not_complete(self):
        payload=json.dumps({"releases":[release()],"links":{"next":""}}).encode()
        with self.assertRaises(WindowError):prepare_window(W,lambda url:payload,fake_prepare)
    def test_incomplete_page_rejected(self):
        payload=json.dumps({'releases':[release()],'links':{'next':'?cursor=AAAA'}}).encode()
        with self.assertRaises(Exception):prepare_window(W,lambda url:payload,fake_prepare,max_pages=1)
    def test_over_ten_not_silently_truncated(self):
        rows=[{**release(),'id':f'r{i}'} for i in range(11)]
        with self.assertRaises(BridgeError):prepare_window(W,lambda url:json.dumps({'releases':rows,'links':{}}).encode(),fake_prepare)
    def test_empty_window_requires_separate_writer(self):
        with self.assertRaises(BridgeError):prepare_window(W,lambda url:b'{"releases":[],"links":{}}',fake_prepare)
    def test_over_hundred_requires_batched_writer(self):
        rows=[{**release(),'id':f'r{i}'} for i in range(101)]
        with self.assertRaises(WindowError):prepare_window(W,lambda url:json.dumps({'releases':rows,'links':{}}).encode(),fake_prepare)
    def test_count_tampering_rejected(self):
        m=prepare_window(W,transport,fake_prepare)
        m['observations']=100
        with self.assertRaisesRegex(BridgeError,'counts_invalid'):
            require_bound_receipt(m,window_start=W.utc_from,window_end=W.utc_to)

class FakeResponse:
    def __init__(self,body):self.body=io.BytesIO(body)
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self,n):return self.body.read(n)

class Transport(unittest.TestCase):
    def test_official_url_only(self):
        with self.assertRaises(TransportError):bounded_get('https://evil.invalid/api/1.0/ocdsReleasePackages',opener=lambda *a,**k:None)
    def test_success(self):
        result=bounded_get('https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages?updatedFrom=x',opener=lambda *a,**k:FakeResponse(b'{}'))
        self.assertEqual(result,b'{}')
    def test_retry_after_integer(self):
        self.assertEqual(retry_after_seconds('17'),17)
        with self.assertRaisesRegex(TransportError,'retry_after_exceeds_budget'):
            retry_after_seconds('500')
    def test_long_retry_after_stops_without_retry(self):
        calls=[]
        def opener(*a,**kw):
            calls.append(1)
            raise HTTPError('https://www.find-tender.service.gov.uk',429,'rate',{'Retry-After':'300'},None)
        with self.assertRaisesRegex(TransportError,'retry_after_exceeds_budget'):
            bounded_get('https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages',opener=opener,sleeper=lambda s:None)
        self.assertEqual(len(calls),1)
    def test_unexpected_redirect_rejected(self):
        def opener(*a,**k):raise HTTPError('https://www.find-tender.service.gov.uk',302,'redirect',{'Location':'https://evil.invalid'},None)
        with self.assertRaisesRegex(TransportError,'source_http_302'):
            bounded_get('https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages',opener=opener,sleeper=lambda s:None)
    def test_429_retry_then_success(self):
        calls=[];sleep=[]
        def opener(*a,**k):
            calls.append(1)
            if len(calls)==1:raise HTTPError('https://www.find-tender.service.gov.uk',429,'rate',{'Retry-After':'3'},None)
            return FakeResponse(b'{}')
        bounded_get('https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages',opener=opener,sleeper=sleep.append)
        self.assertEqual(sleep,[3])
    def test_503_exhaustion(self):
        def opener(*a,**k):raise HTTPError('https://www.find-tender.service.gov.uk',503,'busy',{},None)
        with self.assertRaisesRegex(TransportError,'source_http_503'):
            bounded_get('https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages',opener=opener,sleeper=lambda s:None,max_attempts=2)
    def test_byte_bound(self):
        with self.assertRaisesRegex(TransportError,'response_over_limit'):
            bounded_get('https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages',opener=lambda *a,**k:FakeResponse(b'abcdef'),max_bytes=3)

if __name__=='__main__':unittest.main()
