"""Offline body enrichment checks; no network, credential or database calls."""
import copy
import datetime as dt
import hashlib
import unittest
from unittest.mock import patch

from collectors.inoreader import sync
from collectors.inoreader.body_backfill import BACKFILL_SQL,backfill_missing_body,eligible_capture


def item():
    return sync.normalize({'id':'source-1','title':'Example','canonical':[{'href':'https://example.invalid/one'}],
        'summary':{'content':'<p>Source content for an existing item.</p>'}},'BOHUN_MEDIA',
        captured_at=dt.datetime(2026,10,27,tzinfo=dt.timezone.utc))


class Cursor:
    def __init__(self,row=(1,)):self.row=row;self.calls=[]
    def execute(self,*args):self.calls.append(args)
    def fetchone(self):return self.row


class Backfill(unittest.TestCase):
    def test_valid_provider_body_can_be_proposed(self):
        x=item();self.assertIs(eligible_capture(x),x['raw_data']['content_capture'])
        cur=Cursor();self.assertTrue(backfill_missing_body(cur,7,x,lambda x:x))
        self.assertEqual(cur.calls[0][1][1:],(7,'source-1','https://example.invalid/one','Example','source-1'))

    def test_no_matching_missing_row_is_not_reported_as_updated(self):
        self.assertFalse(backfill_missing_body(Cursor(None),7,item(),lambda x:x))

    def test_metadata_only_never_updates_existing_item(self):
        x=item();x['raw_data']['content_capture']['status']='METADATA_ONLY'
        cur=Cursor();self.assertFalse(backfill_missing_body(cur,7,x,lambda x:x));self.assertEqual(cur.calls,[])

    def test_modified_text_fails_its_hash(self):
        x=item();x['raw_data']['content_capture']['text']='Changed'
        self.assertIsNone(eligible_capture(x))

    def test_modified_html_fails_its_hash(self):
        x=item();x['raw_data']['content_capture']['raw_html']='Changed'
        self.assertIsNone(eligible_capture(x))

    def test_oversized_text_is_not_persisted_by_backfill(self):
        x=item();c=x['raw_data']['content_capture'];c['text']='x'*(128*1024+1);c['text_sha256']=hashlib.sha256(c['text'].encode()).hexdigest()
        self.assertIsNone(eligible_capture(x))

    def test_SQL_preserves_existing_body_and_noncontent_fields(self):
        self.assertIn("jsonb_set(raw_data,'{content_capture}'",BACKFILL_SQL)
        self.assertIn("raw_data->'content_capture' IS NULL",BACKFILL_SQL)
        self.assertNotIn("'BODY_PRESENT_COMPLETENESS_UNVERIFIED'",BACKFILL_SQL)
        self.assertIn('url IS NOT DISTINCT FROM %s',BACKFILL_SQL)
        self.assertIn('title=%s',BACKFILL_SQL)
        self.assertIn("raw_data->>'inoreader_id'=%s",BACKFILL_SQL)

    def test_does_not_change_input_payload(self):
        x=item();before=copy.deepcopy(x);backfill_missing_body(Cursor(),7,x,lambda x:x);self.assertEqual(x,before)

    def test_cli_bound_precedes_credentials(self):
        with patch('sys.argv',['sync.py','--max-items','0']),patch.object(sync,'access_token') as token:
            with self.assertRaises(SystemExit):sync.main()
            token.assert_not_called()

    def test_cli_bounds_collection_across_folders(self):
        x={'id':'source-1','title':'Example','summary':{'content':'<p>Body</p>'}}
        with patch('sys.argv',['sync.py','--max-items','1','--dry-run']),patch.object(sync,'access_token',return_value='fake'),\
             patch.object(sync,'api',return_value={'subscriptions':[{'categories':[{'label':'BOHUN_MEDIA'},{'label':'BOHUN_OTHER'}]}]}),\
             patch.object(sync,'stream_items',return_value=iter([x,{**x,'id':'source-2'}])) as streams,\
             patch.object(sync,'import_items') as writer:
            sync.main();self.assertEqual(streams.call_count,1);writer.assert_not_called()


if __name__=='__main__':unittest.main()
