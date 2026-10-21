import json
import os
import unittest
from unittest.mock import patch
from uk_hold_pilot import PilotError
from uk_live_pilot import prepare_bounded_live
from uk_hold_apply import approval_check,execute_one_transaction
from test_uk_pilot import row,prepare

class LiveTests(unittest.TestCase):
    def test_live_capture_one_page(self):
        p={'releases':[row()],'links':{}}
        def transport(url,**kwargs):
            self.assertIn('updatedFrom=2026-10-15T09',url)
            return json.dumps(p).encode()
        x=prepare_bounded_live(transport=transport)
        self.assertEqual(x['observations'],1)
        self.assertEqual(x['pilot_candidates'],1)
    def test_live_uk_award_not_staged(self):
        p={'releases':[row(tag=('award',)),row(rid='t')],'links':{}}
        x=prepare_bounded_live(transport=lambda url,**kw:json.dumps(p).encode())
        self.assertEqual(x['pilot_candidates'],1)
    def test_live_exceeding_pages_fails_closed(self):
        calls=[]
        def transport(url,**kw):
            calls.append(url)
            return json.dumps({'releases':[],'links':{'next':'?cursor=abc' if len(calls)==1 else '?cursor=def'}}).encode()
        with self.assertRaises(Exception):
            prepare_bounded_live(transport=transport)
    def test_missing_approval_blocks_db_before_connect(self):
        candidate=prepare(row())['candidates'][0]
        with patch.dict(os.environ,{'BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF':'',
                   'BOHUN_UK_HOLD_READERS_AUDIT_REF':'','SUPABASE_DB_PASSWORD':''}):
            with self.assertRaisesRegex(PilotError,'approvals_required'):
                execute_one_transaction([candidate],connect=lambda **kw: (_ for _ in ()).throw(AssertionError('must not connect')))
    def test_audited_reader_required(self):
        with patch.dict(os.environ,{'BOHUN_UK_PILOT_DB_WRITE_APPROVAL_REF':'owner-approved:demo',
                   'BOHUN_UK_HOLD_READERS_AUDIT_REF':''}):
            with self.assertRaisesRegex(PilotError,'approvals_required'):
                approval_check()

if __name__=='__main__':unittest.main()
