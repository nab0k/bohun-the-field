import contextlib
import copy
from datetime import datetime,timezone
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock,patch

from collectors.kb_pilot import cohort as c, stored_cohort as stored, cohort_daily as daily, cohort_runner as runner
from collectors.kb_pilot import embed,pilot,extract
from tests.kb_pilot import test_cohort as fixture

SHA=fixture.SHA


def data_fixture():
    helper=fixture.Tests();helper.calls=[]
    original,h,report,conn=helper.run_write([fixture.row(i) for i in stored.IDS])
    rows=[]
    for i in stored.IDS:
        r=copy.deepcopy(conn.rows[i]);r['raw']['_raw_object']=True;r['publisher_present']=True
        rows.append(r)
    evidence={'schema':'a11-stored-evidence-v1','parent_run':stored.PARENT_RUN,
              'parent_code_sha':SHA,'parent_manifest_sha256':'a'*64,
              'receipts':[{k:r[k] for k in ('item_id','provider_before_sha256','publisher_capture_sha256','publisher_text_sha256')}
                          for r in report['receipts']]}
    return rows,evidence


class Conn:
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def rollback(self):pass


class Tests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack()
        self.stack.enter_context(patch.object(socket,'socket',side_effect=AssertionError('NO NETWORK')))
        self.stack.enter_context(patch.object(socket,'getaddrinfo',side_effect=AssertionError('NO DNS')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.rows,self.evidence=data_fixture()
        self.stack.enter_context(patch.object(stored,'evidence',return_value=self.evidence))
        self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
    def tearDown(self):self.stack.close()

    def test_distinct_stored_manifest_admits_exact_known_ids(self):
        m=stored.freeze(self.rows,SHA);h=c.digest(m)
        self.assertEqual(m['schema'],'a11-stored-cohort-v1');self.assertEqual(m['parent_run'],41439209286)
        c.validate(m,h)
        with self.assertRaises(c.Stop):c.freeze(self.rows,SHA) # Not a forged fetch cohort.
        report=stored.admission_receipt(m,h)
        self.assertEqual(report['http_attempts'],0)
        self.assertTrue(all(r['reason']=='STORED_ADMITTED' for r in report['receipts']))
        self.assertNotIn('WRITTEN',json.dumps(report))

    def test_stored_manifest_cannot_enter_original_fetch_writer(self):
        m=stored.freeze(self.rows,SHA);fetch=Mock();h=c.digest(m)
        with self.assertRaisesRegex(c.Stop,'FETCH_SCHEMA_REQUIRED'):
            c.execute(m,h,None,{r['item_id']:r for r in self.rows},
                      fetch=fetch,extractor=Mock(),pace=Mock(),clock=lambda:0)
        fetch.assert_not_called()

    def test_missing_duplicate_extra_and_wrong_ids_fail(self):
        for rows in (self.rows[:-1],self.rows+[self.rows[0]],self.rows[:-1]+[self.rows[0]]):
            with self.assertRaisesRegex(c.Stop,'ID_SCOPE'):stored.freeze(rows,SHA)

    def test_already_embedded_or_processed_fail_before_any_model(self):
        for key,value in [('embedded',True),('processed',True),('embedding_hash','a'*64),('origin_method','self')]:
            rows=copy.deepcopy(self.rows);rows[0][key]=value
            model=Mock()
            with patch.object(runner,'readonly'),patch.object(runner,'snapshot',return_value={r['item_id']:r for r in rows}):
                with self.assertRaisesRegex(c.Stop,'STATE_NOT_ELIGIBLE'):
                    daily.execute_private(self.temp,resume=True,code_sha=SHA,connect=Conn,
                        embedding=model,synthesis=model,extraction=model)
            model.assert_not_called()

    def test_changed_provider_capture_url_and_source_rejected(self):
        for mutate in (lambda r:r['raw']['content_capture'].update(text='changed'),
                       lambda r:r['raw']['publisher_capture'].update(text='changed'),
                       lambda r:r.update(url='https://elsewhere.example/a'),
                       lambda r:r.update(source_name='Different')):
            rows=copy.deepcopy(self.rows);mutate(rows[0])
            with self.assertRaises(c.Stop):stored.freeze(rows,SHA)

    def test_fresh_snapshot_change_and_manifest_tampering_rejected(self):
        m=stored.freeze(self.rows,SHA);h=c.digest(m);fresh={r['item_id']:copy.deepcopy(r) for r in self.rows}
        stored.revalidate(m,h,fresh)
        fresh[stored.IDS[0]]['title']='edited title'
        with self.assertRaisesRegex(c.Stop,'SNAPSHOT_CHANGED'):stored.revalidate(m,h,fresh)
        m['parent_run']=1
        with self.assertRaises(c.Stop):c.validate(m,c.digest(m))

    def test_no_http_and_no_model_before_second_admission_check(self):
        current={r['item_id']:r for r in self.rows};changed=copy.deepcopy(current)
        changed[stored.IDS[0]]['processed']=True
        model=Mock()
        with patch.object(runner,'readonly'),patch.object(runner,'snapshot',side_effect=[current,changed]), \
             patch.object(c.fulltext.transport,'fetch',side_effect=AssertionError('NO PUBLISHER HTTP')), \
             patch.object(c,'execute',side_effect=AssertionError('NO WRITER')):
            with self.assertRaisesRegex(c.Stop,'STATE_NOT_ELIGIBLE'):
                daily.execute_private(self.temp,resume=True,code_sha=SHA,connect=Conn,
                    embedding=model,synthesis=model,extraction=model)
        model.assert_not_called()

    def test_unknown_outcome_cannot_restart_same_directory(self):
        current={r['item_id']:r for r in self.rows};model=Mock(return_value=1)
        with patch.object(runner,'readonly'),patch.object(runner,'snapshot',return_value=current):
            with self.assertRaisesRegex(c.Stop,'EMBEDDING_FAILED'):
                daily.execute_private(self.temp,resume=True,code_sha=SHA,connect=Conn,embedding=model)
            with self.assertRaisesRegex(c.Stop,'ATTEMPT_ALREADY_STARTED'):
                daily.execute_private(self.temp,resume=True,code_sha=SHA,connect=Conn,embedding=model)
        self.assertEqual(model.call_count,1)

    def test_incomplete_index_stops_before_pilot_and_keeps_diagnostics(self):
        current={r['item_id']:copy.deepcopy(r) for r in self.rows}
        after=copy.deepcopy(current)
        for r in after.values():r['published_at']=datetime.fromisoformat(r['published_at'])
        model=Mock();evidence=self.temp/'audit.json'
        with patch.object(runner,'readonly'),patch.object(runner,'snapshot',side_effect=[current,current,after]):
            with self.assertRaisesRegex(c.Stop,'INDEX_READBACK_FAILED'):
                daily.execute_private(self.temp,resume=True,evidence_path=evidence,code_sha=SHA,connect=Conn,
                    embedding=lambda:0,synthesis=model,extraction=model)
        model.assert_not_called();audit=json.loads(evidence.read_text())
        self.assertEqual(audit['stage'],'INDEX_READBACK');self.assertEqual(audit['stop'],'INDEX_READBACK_FAILED_NO_MODELS')
        self.assertEqual(audit['http_attempts'],0);self.assertEqual(audit['reader_receipts'],[])

    def test_scoped_reader_accepts_stored_schema_but_not_changed_body(self):
        m=stored.freeze(self.rows,SHA);h=c.digest(m);sink=[]
        expected={r['item_id']:r['publisher_text_sha256'] for r in self.evidence['receipts']}
        with patch.object(pilot,'load_items',return_value=[{'item_id':stored.IDS[0],'body_source_kind':'publisher_article','body_text_sha256':'wrong'}]):
            with daily.scoped_readers(m,h,SHA,sink,expected):
                with self.assertRaisesRegex(c.Stop,'READER_BODY_CHANGED'):pilot.load_items(None,'a','b')
        self.assertFalse(sink)

    def test_actual_reader_receipts_and_snapshot_replays_remain_distinct(self):
        current={r['item_id']:copy.deepcopy(r) for r in self.rows};indexed=copy.deepcopy(current)
        for r in indexed.values():
            r.update(embedded=True,embedding_hash=c.body.sha(embed.item_text(r['title'],r['raw']['publisher_capture']['text'])),
                     origin_method='self',origin_item_id=r['item_id'])
            r['published_at']=datetime.fromisoformat(r['published_at'])
        calls=[]
        def emb():
            calls.append('embed')
            rows=[(r['item_id'],r['title'],r['url'],r['raw'],datetime.fromisoformat(r['published_at'])) for r in current.values()]
            embed.pending(c.RowsCursor(rows),10)
            return 0
        def synthesis(args):
            calls.append('pilot');self.assertIn('--publish-hold',args);self.assertIn('--new-only',args)
            rows=[(r['item_id'],r['title'],r['url'],r['published_at'],r['raw'],r['embedding_hash'],r['origin_method']) for r in indexed.values()]
            pilot.load_items(c.RowsCursor(rows),'2026-11-01','2026-11-01',new_only=True)
            return 0
        def extraction(args):
            calls.append('extract')
            rows=[(r['item_id'],r['title'],r['url'],r['raw'],r['published_at'],r['origin_item_id'],r['origin_method'],r['embedding_hash']) for r in indexed.values()]
            extract.pending(c.RowsCursor(rows),'2026-11-01','2026-11-01',10)
            return 0
        with patch.object(runner,'readonly'),patch.object(runner,'snapshot',side_effect=[current,current,indexed,indexed]), \
             patch.object(c.fulltext.transport,'fetch',side_effect=AssertionError('NO HTTP')), \
             patch.object(c,'execute',side_effect=AssertionError('NO CAPTURE WRITER')):
            result,rc=daily.execute_private(self.temp,resume=True,evidence_path=self.temp/'audit.json',code_sha=SHA,
                connect=Conn,embedding=emb,synthesis=synthesis,extraction=extraction)
        self.assertEqual(calls,['embed','pilot','extract']);self.assertEqual(result['end_to_end_proven'],10)
        self.assertEqual(rc,2);self.assertEqual(result['status'],'PARTIAL');self.assertEqual(result['manual_reviewed'],0)
        audit=json.loads((self.temp/'audit.json').read_text())
        self.assertEqual(len(audit['reader_receipts']),30) # Snapshot replays created no extra receipts.
        self.assertEqual(audit['http_attempts'],0)
        for forbidden in ('publisher.example','content_capture','A substantive','Title'):
            self.assertNotIn(forbidden,json.dumps(audit))

    def test_resume_main_rejects_wrong_sha_before_connection(self):
        env={'GITHUB_EVENT_NAME':'workflow_dispatch','A11_MANUAL_RESUME':'true','A11_EXPECTED_SHA':'wrong'}
        with patch.dict(os.environ,env,clear=True),patch.object(runner,'current_sha',return_value=SHA),patch.object(daily,'execute_private') as execute:
            with self.assertRaises(SystemExit):daily.main(['--apply','--resume-stored'])
            execute.assert_not_called()

    def test_safe_diagnostics_do_not_leak_exception_text(self):
        with patch.object(daily,'_resume_private',side_effect=RuntimeError('SECRET url/private')):
            with self.assertRaises(RuntimeError):daily.execute_private(self.temp,resume=True,evidence_path=self.temp/'audit.json')
        text=(self.temp/'audit.json').read_text();self.assertNotIn('SECRET',text)
        self.assertEqual(json.loads(text)['stop'],'UNKNOWN')

    def test_resume_workflow_is_optin_pinned_exclusive_and_does_not_install_extractor(self):
        text=Path('.github/workflows/kb-daily.yml').read_text()
        self.assertIn('resume_stored_cohort:',text);self.assertEqual(text.count('default: false'),2)
        self.assertIn('--apply --resume-stored',text)
        start=text.index('      - name: Resume the exact');end=text.index('      - name:',start+10)
        self.assertNotIn('pip install',text[start:end])
        self.assertLess(text.index('Validate exclusive manual verification inputs'),start)
        self.assertIn("os.environ['EXPECTED_SHA'] != os.environ['GITHUB_SHA']",text)
        self.assertEqual(text.count('inputs.resume_stored_cohort != true'),4)
        self.assertEqual(text.count('cron:'),2)
        self.assertIn('unittest discover -s tests/kb_pilot -t .',text)



if __name__=='__main__':unittest.main()
