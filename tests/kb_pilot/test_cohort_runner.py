import contextlib
import copy
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import Mock, patch

from collectors.kb_pilot import cohort
from collectors.kb_pilot import cohort_runner as runner
from tests.kb_pilot.test_cohort import row, manifest, SHA


class Conn:
    def __init__(self,ids=(),rows=()): self.ids=ids; self.rows=rows; self.calls=[]; self.rollbacks=0
    def __enter__(self): return self
    def __exit__(self,*_): return False
    def cursor(self): return self
    def execute(self,sql,args=()): self.calls.append((sql,args))
    def fetchall(self):
        return [(i,) for i in self.ids] if self.calls[-1][0]==runner.SELECT_IDS_SQL else [(copy.deepcopy(r),) for r in self.rows]
    def rollback(self): self.rollbacks+=1


class Tests(unittest.TestCase):
    def setUp(self):
        self.stack=contextlib.ExitStack()
        self.stack.enter_context(patch.object(socket,'socket',side_effect=AssertionError('NO NETWORK')))
        self.stack.enter_context(patch.object(socket,'getaddrinfo',side_effect=AssertionError('NO DNS')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
        self.temp=self.stack.enter_context(tempfile.TemporaryDirectory())
        self.m=Path(self.temp)/'manifest.json'; self.report=Path(self.temp)/'result.json'
    def tearDown(self): self.stack.close()

    def test_no_candidates_has_nonzero_exit_and_creates_no_manifest(self):
        conn=Conn()
        rc=runner.main(['select','--manifest',str(self.m),'--report',str(self.report),
                        '--from-utc','2026-10-31T00:00:00Z','--to-utc','2026-11-02T00:00:00Z'],
                       connect=lambda:conn,code_sha=SHA)
        self.assertEqual(rc,2); self.assertFalse(self.m.exists())
        self.assertEqual(json.loads(self.report.read_text())['status'],'INCONCLUSIVE')
        self.assertTrue(all(sql.startswith(('SELECT','SET')) for sql,_ in conn.calls))
        self.assertEqual(conn.rollbacks,1)

    def test_snapshot_requires_exact_small_id_scope(self):
        conn=Conn(rows=[row()])
        for ids in ([],[1,1],list(range(13)),[True]):
            with self.assertRaises(cohort.Stop):runner.snapshot(conn,ids)
        self.assertEqual(conn.calls,[])
        self.assertEqual(set(runner.snapshot(conn,[1])),{1})
        self.assertEqual(conn.calls[-1][1],([1],))

    def test_json_files_private_and_atomically_replaced(self):
        runner.private_json(self.m,{'value':1});runner.private_json(self.m,{'value':2})
        self.assertEqual(json.loads(self.m.read_text()),{'value':2})
        self.assertEqual(self.m.stat().st_mode&0o777,0o600)
        self.assertEqual(list(Path(self.temp).glob('.a11-*')),[])

    def test_code_sha_and_apply_guards_precede_connection(self):
        m,h=manifest();runner.private_json(self.m,m);connect=Mock()
        args=['write','--manifest',str(self.m),'--report',str(self.report),'--expected-manifest-sha',h]
        with self.assertRaisesRegex(cohort.Stop,'CODE_SHA_CHANGED'):runner.main(args+['--apply'],connect=connect,code_sha='bad')
        with self.assertRaises(SystemExit):runner.main(args,connect=connect,code_sha=SHA)
        connect.assert_not_called()

    def test_select_is_readonly_and_freezes_exact_ids(self):
        conn=Conn(ids=[1],rows=[row()])
        rc=runner.main(['select','--manifest',str(self.m),'--report',str(self.report),
                        '--from-utc','2026-10-31T00:00:00Z','--to-utc','2026-11-02T00:00:00Z'],
                       connect=lambda:conn,code_sha=SHA)
        frozen=json.loads(self.m.read_text());self.assertEqual(rc,0)
        self.assertEqual([r['item_id'] for r in frozen['rows']],[1])
        self.assertIn(('SET TRANSACTION READ ONLY',()),conn.calls)
        self.assertEqual(json.loads(self.report.read_text())['manifest_sha256'],cohort.digest(frozen))

    def test_started_manifest_cannot_be_retried_even_if_process_result_unknown(self):
        m,h=manifest();runner.private_json(self.m,m)
        conn=Conn(rows=[row()])
        result={'receipts':[{'item_id':1,'reason':'WRITTEN'}],'stop':None}
        args=['write','--manifest',str(self.m),'--report',str(self.report),'--expected-manifest-sha',h,'--apply']
        with patch.object(cohort,'execute',return_value=result) as execute:
            self.assertEqual(runner.main(args,connect=lambda:conn,code_sha=SHA),0)
            with self.assertRaisesRegex(cohort.Stop,'ALREADY_STARTED'):
                runner.main(args,connect=lambda:conn,code_sha=SHA)
            self.assertEqual(execute.call_count,1)


if __name__=='__main__':unittest.main()
