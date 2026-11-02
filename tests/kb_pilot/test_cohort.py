import contextlib
import copy
from datetime import datetime, timezone
import io
import json
import socket
import unittest
from unittest.mock import patch

from collectors.kb_pilot import cohort as c

SHA = '82dd0c4617663f640bdf30be487adda258fe196c'
TEXT = 'A substantive article from the publisher. ' * 15


def row(i=1):
    provider = {'version': 1, 'provider_field': 'summary.content', 'status': 'BODY_PRESENT_COMPLETENESS_UNVERIFIED',
                'text': 'short', 'text_sha256': c.body.sha('short')}
    return {'item_id': i, 'source_id': 2, 'source_name': 'Inoreader BOHUN', 'item_type': 'news',
            'title': 'Title', 'url': 'https://publisher.example/article' + str(i),
            'published_at': datetime(2026,11,1,tzinfo=timezone.utc).isoformat(),
            'raw': {'content_capture': provider}, 'provider_present': True, 'publisher_present': False,
            'embedded': False, 'processed': False, 'origin_method': None}


def manifest(rows=None):
    m = c.freeze(rows if rows is not None else [row()], SHA)
    return m, c.digest(m)


class FakeConnection:
    def __init__(self, rows, *, stale=False, error_on=None):
        self.rows = copy.deepcopy(rows); self.stale = stale; self.error_on = error_on
        self.calls = []; self.commits = self.rollbacks = 0; self.pending = None; self.rowcount = 0
    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def execute(self, sql, args):
        self.calls.append((sql,args))
        iid = args[1]
        if iid == self.error_on: raise RuntimeError('SECRET')
        self.rowcount = 0 if self.stale else 1
        self.pending = None if self.stale else (iid, json.loads(args[0]))
    def commit(self):
        self.commits += 1
        if self.pending:
            iid, cap = self.pending
            self.rows[iid]['raw']['publisher_capture'] = cap
            self.rows[iid]['publisher_present'] = True
    def rollback(self): self.rollbacks += 1; self.pending = None


class Tests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(socket,'socket',side_effect=AssertionError('NO NETWORK')))
        self.stack.enter_context(patch.object(socket,'getaddrinfo',side_effect=AssertionError('NO DNS')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.calls = []
    def tearDown(self): self.stack.close()

    def fetch(self,url,*_args,**_kwargs):
        self.calls.append(url)
        return {'status':200,'url':url,'body': b'User-agent: *\nAllow: /' if url.endswith('robots.txt') else b'article'}

    def run_write(self, rows=None, **conn_kwargs):
        rows = rows or [row()]; m,h = manifest(rows); current = {r['item_id']:r for r in rows}
        conn = FakeConnection(current,**conn_kwargs)
        result = c.execute(m,h,conn,current,fetch=self.fetch,extractor=lambda *_:TEXT,pace=lambda *_:None,clock=lambda:0)
        return m,h,result,conn

    def after(self,conn):
        out = copy.deepcopy(conn.rows)
        for r in out.values():
            r['published_at'] = datetime.fromisoformat(r['published_at'])
            cap = r['raw'].get('publisher_capture')
            if cap:
                r['embedding_hash'] = c.body.sha(c.embed.item_text(r['title'],cap['text']))
                r['origin_method'] = 'self'; r['origin_item_id'] = r['item_id']
        return out

    def test_empty_selection_is_not_a_valid_write_manifest(self):
        for rows in ([],[row(i) for i in range(13)]):
            with self.assertRaisesRegex(c.Stop,'1_TO_12'): c.freeze(rows,SHA)
        self.assertEqual(self.calls,[])

    def test_duplicate_ineligible_or_processed_items_rejected(self):
        with self.assertRaisesRegex(c.Stop,'DUPLICATE'): c.freeze([row(),row()],SHA)
        for field,value in [('embedded',True),('processed',True),('source_name','Other'),('item_type','tender'),
                            ('url','http://publisher.example/a'),('origin_method','near_duplicate')]:
            r = row(); r[field] = value
            with self.subTest(field=field),self.assertRaises(c.Stop): c.freeze([r],SHA)

    def test_manifest_or_current_snapshot_change_stops_before_requests(self):
        m,h = manifest(); conn = FakeConnection({1:row()})
        changed = copy.deepcopy(m); changed['rows'][0]['url'] += 'changed'
        with self.assertRaisesRegex(c.Stop,'MANIFEST_CHANGED'):
            c.execute(changed,h,conn,conn.rows,fetch=self.fetch,extractor=lambda *_:TEXT,pace=lambda *_:None,clock=lambda:0)
        fresh = {1:row()}; fresh[1]['raw']['content_capture']['text'] = 'edited'
        with self.assertRaisesRegex(c.Stop,'SNAPSHOT_CHANGED'):
            c.execute(m,h,conn,fresh,fetch=self.fetch,extractor=lambda *_:TEXT,pace=lambda *_:None,clock=lambda:0)
        self.assertEqual((self.calls,conn.calls),([],[]))

    def test_wrong_current_id_set_is_not_replaced(self):
        m,h = manifest()
        with self.assertRaisesRegex(c.Stop,'ID_SET'):
            c.execute(m,h,FakeConnection({}),{2:row(2)},fetch=self.fetch,extractor=lambda *_:TEXT,pace=lambda *_:None,clock=lambda:0)
        self.assertEqual(self.calls,[])

    def test_exact_twelve_and_robots_cache_bounded(self):
        m,h,result,conn = self.run_write([row(i) for i in range(1,13)])
        self.assertEqual({r['item_id'] for r in result['receipts']},set(range(1,13)))
        self.assertEqual(result['http_attempts'],13)
        self.assertEqual(conn.commits,12)
        self.assertEqual(result['not_attempted_ids'],[])
        self.assertTrue(all(r['reason']=='WRITTEN' for r in result['receipts']))

    def test_stale_writer_and_zero_success_are_inconclusive(self):
        m,h,result,conn = self.run_write(stale=True)
        verified = c.verify(m,h,result,self.after(conn))
        self.assertEqual((verified['status'],verified['stored']),('INCONCLUSIVE',0))
        self.assertEqual(result['receipts'][0]['reason'],'STALE_NOT_WRITTEN')

    def test_partial_failure_no_retry(self):
        m,h,result,conn = self.run_write([row(1),row(2),row(3)],error_on=2)
        self.assertEqual((result['stop'],result['not_attempted_ids']),('PUBLISHER_WRITE_FAILED',[3]))
        self.assertEqual((conn.commits,conn.rollbacks,len(conn.calls)),(1,1,2))
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertEqual(c.verify(m,h,result,self.after(conn))['status'],'PARTIAL')

    def test_provider_or_identity_readback_change_fails(self):
        m,h,result,conn = self.run_write()
        for field in ('source_id','url'):
            after = self.after(conn); after[1][field] = 'changed'
            self.assertEqual(c.verify(m,h,result,after)['status'],'FAILED')
        after = self.after(conn); after[1]['raw']['content_capture']['text'] = 'changed'
        self.assertEqual(c.verify(m,h,result,after)['status'],'FAILED')

    def test_publisher_readback_hash_change_fails(self):
        m,h,result,conn = self.run_write(); after = self.after(conn)
        after[1]['raw']['publisher_capture']['text_sha256'] = 'bad'
        self.assertEqual(c.verify(m,h,result,after)['status'],'FAILED')

    def test_storage_without_embedding_remains_partial(self):
        m,h,result,conn = self.run_write(); after = self.after(conn); after[1]['embedding_hash'] = 'old'
        verified = c.verify(m,h,result,after)
        self.assertEqual((verified['status'],verified['stored']),('PARTIAL',1))
        self.assertFalse(verified['proofs'][0]['reader_replay_ok'])

    def test_snapshot_replay_is_not_historical_execution_proof(self):
        m,h,result,conn = self.run_write(); verified = c.verify(m,h,result,self.after(conn),manual_reviewed_ids=[1])
        self.assertEqual(verified['status'],'PARTIAL')
        self.assertTrue(verified['snapshot_replay_only'])
        self.assertTrue(verified['proofs'][0]['reader_replay_ok'])
        self.assertEqual(verified['end_to_end_proven'],0)

    def test_receipt_id_or_manifest_mismatch_is_rejected(self):
        m,h,result,conn = self.run_write()
        wrong = copy.deepcopy(result); wrong['receipts'][0]['item_id'] = 9
        with self.assertRaisesRegex(c.Stop,'RECEIPT_ID'): c.verify(m,h,wrong,self.after(conn))
        wrong = copy.deepcopy(result); wrong['manifest_sha256'] = 'changed'
        with self.assertRaisesRegex(c.Stop,'RUN_RECEIPT'): c.verify(m,h,wrong,self.after(conn))

    def test_runtime_receipts_and_manual_review_required_for_success(self):
        m,h,result,conn = self.run_write(); after = self.after(conn)
        receipts = [{'item_id':1,'consumer':consumer,'source_kind':'publisher_article',
                     'body_sha256':c.body.sha(TEXT),'run_sha':SHA,'observed_at_call':True,
                     'manifest_sha256':h,'payload_sha256':c.body.sha('Title'+ ('\n' if consumer=='pilot' else '\n\n')+TEXT)}
                    for consumer in ('pilot','extract')]
        self.assertEqual(c.verify(m,h,result,after,runtime_receipts=receipts)['status'],'PARTIAL')
        self.assertEqual(c.verify(m,h,result,after,runtime_receipts=receipts,manual_reviewed_ids=[1])['status'],'SUCCEEDED')
        receipts[0]['run_sha'] = 'wrong'
        self.assertEqual(c.verify(m,h,result,after,runtime_receipts=receipts,manual_reviewed_ids=[1])['status'],'PARTIAL')

    def test_observer_records_only_actual_selected_reader_calls(self):
        m,h = manifest(); sink = []
        outputs = [{'item_id':1,'text':TEXT,'body_source_kind':'publisher_article','body_text_sha256':c.body.sha(TEXT)},
                   {'item_id':9,'text':'not in cohort'}]
        wrapped = c.observe_reader(lambda: outputs,'pilot',m,h,SHA,sink)
        self.assertEqual(sink,[])
        self.assertIs(wrapped(),outputs)
        self.assertEqual(len(sink),1)
        self.assertEqual(sink[0]['payload_sha256'],c.body.sha(TEXT))
        self.assertEqual(sink[0]['manifest_sha256'],h)
        self.assertNotIn('text',sink[0])

    def test_failed_reader_emits_no_false_runtime_receipt(self):
        m,h = manifest(); sink = []
        def broken(): raise ValueError('reader failed')
        wrapped = c.observe_reader(broken,'extract',m,h,SHA,sink)
        with self.assertRaises(ValueError): wrapped()
        self.assertEqual(sink,[])
        with self.assertRaisesRegex(c.Stop,'SCOPE_CHANGED'):
            c.observe_reader(broken,'extract',m,h,'wrong',sink)


if __name__=='__main__': unittest.main()
