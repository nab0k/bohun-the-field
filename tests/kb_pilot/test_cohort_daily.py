import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from collectors.kb_pilot import cohort_daily as daily, cohort, cohort_runner as runner, embed, pilot, extract
from tests.kb_pilot.test_cohort import manifest, SHA
from tests.kb_pilot import test_cohort as fixtures


class Cursor:
    def __init__(self): self.calls=[]
    def execute(self,sql,args): self.calls.append((sql,args))
    def fetchall(self): return []


class Tests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack()
        self.stack.enter_context(patch.object(socket,'socket',side_effect=AssertionError('NO NETWORK')))
        self.stack.enter_context(patch.object(socket,'getaddrinfo',side_effect=AssertionError('NO DNS')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
    def tearDown(self): self.stack.close()

    def test_scope_is_before_limit_and_parameters_are_bound(self):
        for reader,args in ((embed.pending,(12,)),(pilot.load_items,('2026-11-01','2026-11-01')),
                            (extract.pending,('2026-11-01','2026-11-01',12))):
            cur=Cursor(); reader(cur,*args,item_ids=[7,9])
            sql,params=cur.calls[0]
            self.assertEqual(params[0],[7,9]);self.assertIn('i.id = ANY(%s)',sql)
            self.assertLess(sql.index('i.id = ANY'),sql.index('ORDER BY'))
            self.assertNotIn('7',sql)
            for bad in ([],[True],[1,1],list(range(1,14))):
                cur=Cursor()
                with self.assertRaises(cohort.Stop):reader(cur,*args,item_ids=bad)
                self.assertFalse(cur.calls)

    def test_default_readers_do_not_add_scope_or_change_parameters(self):
        cur=Cursor();embed.pending(cur,400,'2026-11-01')
        self.assertEqual(cur.calls[0][1],('2026-11-01','2026-11-01',400))
        self.assertNotIn('i.id = ANY',cur.calls[0][0])

    def test_scoped_observer_runs_on_actual_calls_and_restores_after_failure(self):
        m,h=manifest();receipts=[];originals=(embed.pending,pilot.load_items,extract.pending,embed.run)
        with self.assertRaisesRegex(RuntimeError,'stop'):
            with daily.scoped_readers(m,h,SHA,receipts):
                for reader,args in ((embed.pending,(12,)),(pilot.load_items,('2026-11-01','2026-11-01')),
                                    (extract.pending,('2026-11-01','2026-11-01',12))):
                    cur=Cursor();reader(cur,*args);self.assertEqual(cur.calls[0][1][0],[1])
                raise RuntimeError('stop')
        self.assertEqual(originals,(embed.pending,pilot.load_items,extract.pending,embed.run))
        self.assertEqual(receipts,[]) # Empty readers do not fabricate consumed inputs.

    def test_new_only_embedding_conflict_never_updates_existing_origin(self):
        class Conn(Cursor):
            rowcount=0
            def cursor(self): return self
            def __enter__(self): return self
            def __exit__(self,*_): pass
            def commit(self): pass
        conn=Conn()
        item={'item_id':1,'text':'Text','published':datetime.now(timezone.utc)}
        with patch.object(embed,'pending',return_value=[item]),patch.object(embed,'neighbours') as neighbours:
            result=embed.run(conn,'','',backend='local',local=lambda _:[[1.0]],new_only=True)
        neighbours.assert_not_called();self.assertEqual(result['embedded'],0)
        self.assertIn('DO NOTHING',conn.calls[0][0]);self.assertNotIn('DO UPDATE',conn.calls[0][0])
        self.assertEqual(len(conn.calls),1)

    def test_zero_selection_stops_before_http_and_all_models(self):
        with tempfile.TemporaryDirectory() as temp:
            command=Mock(return_value=2);models=[Mock(),Mock(),Mock()];connect=Mock()
            result,rc=daily.execute_private(temp,code_sha=SHA,now=datetime(2026,11,1,tzinfo=timezone.utc),
                command=command,connect=connect,embedding=models[0],synthesis=models[1],extraction=models[2])
            self.assertEqual(rc,2);self.assertEqual(result['status'],'INCONCLUSIVE')
            self.assertEqual(command.call_count,1);connect.assert_not_called()
            for model in models:model.assert_not_called()

    def test_partial_capture_stops_before_any_model(self):
        m,h=manifest()
        with tempfile.TemporaryDirectory() as temp:
            def command(args,**_):
                if args[0]=='select':runner.private_json(Path(temp)/'manifest.json',m);return 0
                return 2
            model=Mock();connect=Mock()
            result,rc=daily.execute_private(temp,code_sha=SHA,now=datetime(2026,11,1,tzinfo=timezone.utc),
                command=command,connect=connect,embedding=model,synthesis=model,extraction=model)
            self.assertEqual(rc,2);self.assertEqual(result['reason'],'CAPTURE_INCOMPLETE')
            connect.assert_not_called();model.assert_not_called()

    def test_bad_readback_prevents_models(self):
        fixture=fixtures.Tests();fixture.calls=[]
        m,h,report,conn=fixture.run_write();after=fixture.after(conn)
        self.assertEqual(daily.storage_gate(m,h,report,after)['stored'],1)
        after[1]['raw']['content_capture']['text']='changed'
        with self.assertRaisesRegex(cohort.Stop,'INCOMPLETE_STORAGE'):daily.storage_gate(m,h,report,after)

    def test_manual_mode_requires_explicit_workflow_optin_before_any_work(self):
        with patch.dict(os.environ,{},clear=True),patch.object(daily,'execute_private') as execute:
            with self.assertRaises(SystemExit):daily.main(['--apply'])
            execute.assert_not_called()

    def test_workflow_default_cron_and_private_output_boundary(self):
        workflow=Path('.github/workflows/kb-daily.yml').read_text()
        self.assertIn('default: false',workflow)
        self.assertIn("cron: '20 6 * * *'",workflow);self.assertIn("cron: '20 15 * * *'",workflow)
        self.assertLess(workflow.index('One manual fulltext cohort'),workflow.index('Embeddings of new items'))
        self.assertEqual(workflow.count("inputs.verify_fulltext_cohort != true"),4)
        self.assertIn('path: ${{ runner.temp }}/kb-daily',workflow)
        self.assertNotIn('a11-private-',workflow)

    def test_orchestration_order_scope_and_no_manual_quality_shortcut(self):
        fixture=fixtures.Tests();fixture.calls=[]
        m,h,report,conn=fixture.run_write();after=fixture.after(conn)
        class Conn:
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def rollback(self):pass
        with tempfile.TemporaryDirectory() as temp:
            calls=[]
            def command(args,**_):
                calls.append(args[0])
                if args[0]=='select': runner.private_json(Path(temp)/'manifest.json',m)
                elif args[0]=='write': runner.private_json(Path(temp)/'capture.json',report)
                else:
                    runner.private_json(Path(temp)/'capture.json.verified.json',{'status':'PARTIAL','stored':1,
                        'end_to_end_proven':1,'manual_reviewed':0})
                return 0
            def model(name):
                def run(*args):
                    calls.append(name)
                    self.assertIsNot(pilot.load_items,original)
                    if name=='pilot':self.assertIn('--publish-hold',args[0])
                    return 0
                return run
            original=pilot.load_items
            with patch.object(runner,'readonly'),patch.object(runner,'snapshot',return_value=after):
                result,rc=daily.execute_private(temp,code_sha=SHA,now=datetime(2026,11,1,tzinfo=timezone.utc),
                    command=command,connect=Conn,embedding=model('embed'),synthesis=model('pilot'),extraction=model('extract'))
            self.assertEqual(calls,['select','write','embed','pilot','extract','verify'])
            self.assertEqual(rc,2);self.assertEqual(result['manual_reviewed'],0)
            self.assertIs(pilot.load_items,original)
            self.assertEqual(json.loads((Path(temp)/'reader-receipts.json').read_text()),[])

    def test_audit_allowlist_excludes_all_raw_text_and_survives_failure(self):
        m,h=manifest()
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);runner.private_json(path/'manifest.json',m)
            runner.private_json(path/'capture.json',{'secret':'DO_NOT_EXPORT','receipts':[
                {'item_id':1,'text':'DO_NOT_EXPORT','publisher_text_sha256':'a'*64}]})
            runner.private_json(path/'reader-receipts.json',[
                {'item_id':1,'consumer':'pilot','text':'DO_NOT_EXPORT','body_sha256':'b'*64}])
            audit=daily.metadata_only(temp);encoded=json.dumps(audit)
            for forbidden in ('DO_NOT_EXPORT','publisher.example','content_capture','Title','short'):
                self.assertNotIn(forbidden,encoded)
            self.assertEqual(audit['item_ids'],[1]);self.assertEqual(audit['manual_reviewed'],0)
            evidence=path/'audit.json'
            with patch.object(daily,'_execute_private',side_effect=RuntimeError('unknown')):
                with self.assertRaises(RuntimeError):daily.execute_private(temp,evidence_path=evidence)
            saved=json.loads(evidence.read_text());audit.update(stage='ADMISSION',stop='UNKNOWN')
            self.assertEqual(saved,audit)
            self.assertEqual(evidence.stat().st_mode&0o777,0o600)

    def test_model_failure_restores_readers_and_saves_no_false_success(self):
        fixture=fixtures.Tests();fixture.calls=[]
        m,h,report,conn=fixture.run_write();after=fixture.after(conn)
        class Conn:
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def rollback(self):pass
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp);original=embed.pending
            def command(args,**_):
                runner.private_json(path/('manifest.json' if args[0]=='select' else 'capture.json'),
                                    m if args[0]=='select' else report)
                return 0
            remaining=Mock()
            with patch.object(runner,'readonly'),patch.object(runner,'snapshot',return_value=after):
                with self.assertRaisesRegex(cohort.Stop,'EMBEDDING_FAILED'):
                    daily.execute_private(temp,evidence_path=path/'audit.json',code_sha=SHA,
                        now=datetime(2026,11,1,tzinfo=timezone.utc),command=command,connect=Conn,
                        embedding=lambda:1,synthesis=remaining,extraction=remaining)
            remaining.assert_not_called();self.assertIs(embed.pending,original)
            self.assertEqual(json.loads((path/'audit.json').read_text())['status'],'INCONCLUSIVE')
            self.assertTrue((path/'reader-receipts.json').exists())

    def test_changed_runtime_body_is_rejected_before_receipt_or_model(self):
        m,h=manifest();receipts=[]
        bad=[{'item_id':1,'text':'changed','body_source_kind':'publisher_article','body_text_sha256':'b'*64}]
        with patch.object(pilot,'load_items',return_value=bad):
            with daily.scoped_readers(m,h,SHA,receipts,{1:'a'*64}):
                with self.assertRaisesRegex(cohort.Stop,'READER_BODY_CHANGED'):
                    pilot.load_items(None,'2026-11-01','2026-11-01')
        self.assertEqual(receipts,[])

    def test_missing_current_index_stops_before_synthesis(self):
        fixture=fixtures.Tests();fixture.calls=[]
        m,h,report,conn=fixture.run_write();after=fixture.after(conn)
        stale=fixtures.copy.deepcopy(after);stale[1]['embedding_hash']='old'
        class Conn:
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def rollback(self):pass
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)
            def command(args,**_):
                runner.private_json(path/('manifest.json' if args[0]=='select' else 'capture.json'),
                                    m if args[0]=='select' else report)
                return 0
            model=Mock()
            with patch.object(runner,'readonly'),patch.object(runner,'snapshot',side_effect=[after,stale]):
                with self.assertRaisesRegex(cohort.Stop,'INDEX_READBACK_FAILED'):
                    daily.execute_private(temp,code_sha=SHA,now=datetime(2026,11,1,tzinfo=timezone.utc),
                        command=command,connect=Conn,embedding=lambda:0,synthesis=model,extraction=model)
            model.assert_not_called()
            self.assertEqual(json.loads((path/'reader-receipts.json').read_text()),[])


if __name__=='__main__':unittest.main()
