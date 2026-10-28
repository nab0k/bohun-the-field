"""Client transaction fault tests. These doubles do NOT execute or validate SQL."""
import json
from unittest import TestCase
from unittest.mock import Mock

from collectors.intelligence_pipeline.postgres import RuntimeAPI, DatabaseUnknown, PostgresStore, PostgresQuota
from collectors.intelligence_pipeline.ports import LeaseLost, QuotaExceeded
from collectors.intelligence_pipeline.contracts import Usage


class Connection:
    def __init__(self, *, role=("bohun_runtime","bohun_runtime",True), result=None, lost=False):
        self.role=role; self.result=result or {"status":"OK","value":None}
        self.lost=lost; self.autocommit=False; self.queries=[]; self.commits=0; self.rollbacks=0; self.closed=False
    def execute(self, sql, params=None):
        self.queries.append((sql,params)); self.row=(self.result,) if 'runtime_api' in sql else self.role
        return self
    def fetchone(self): return self.row
    def commit(self):
        self.commits+=1
        if self.lost: raise OSError('PRIVATE_PASSWORD_SENTINEL')
    def rollback(self): self.rollbacks+=1
    def close(self): self.closed=True


class APIClientTests(TestCase):
    def test_write_uses_bound_json_and_one_commit(self):
        c=Connection(); factory=Mock(return_value=c)
        RuntimeAPI(factory).call('receive',{'body':"PRIVATE'; DROP TABLE x"})
        self.assertEqual(c.commits,1); self.assertTrue(c.closed); factory.assert_called_once()
        sql,args=c.queries[-1]
        self.assertNotIn('PRIVATE',sql); self.assertEqual(json.loads(args[1])['body'],"PRIVATE'; DROP TABLE x")
    def test_read_ends_readonly_without_commit(self):
        c=Connection(); RuntimeAPI(lambda:c).call('context',{},readonly=True)
        self.assertEqual(c.queries[0][0],'SET TRANSACTION READ ONLY')
        self.assertEqual((c.commits,c.rollbacks),(0,1))
    def test_wrong_role_or_unencrypted_connection_never_calls_function(self):
        for role in (('postgres','postgres',True),('bohun_runtime','postgres',True),('bohun_runtime','bohun_runtime',False)):
            c=Connection(role=role)
            with self.subTest(role=role),self.assertRaises(RuntimeError): RuntimeAPI(lambda:c).call('claim',{})
            self.assertFalse(any('runtime_api' in q for q,p in c.queries));self.assertEqual(c.commits,0)
    def test_lost_commit_ack_is_not_retried_or_reported_with_private_details(self):
        c=Connection(lost=True);factory=Mock(return_value=c)
        with self.assertRaises(DatabaseUnknown) as error: RuntimeAPI(factory).call('finish',{})
        self.assertNotIn('PRIVATE',str(error.exception));self.assertEqual(c.commits,1);factory.assert_called_once()
    def test_stale_and_quota_stop_remain_typed_after_commit(self):
        for status,error in [('STALE',LeaseLost),('QUOTA_BLOCKED',QuotaExceeded)]:
            c=Connection(result={'status':status})
            with self.subTest(status=status),self.assertRaises(error): RuntimeAPI(lambda:c).call('finish',{})
            self.assertEqual(c.commits,1)
    def test_quota_does_not_reset_or_invent_daily_budget(self):
        from test_costing import BudgetAPI,approval
        access=approval();api=BudgetAPI(access);q=PostgresQuota(api,'one-reviewed-run',access)
        q.reserve('request',None,Usage(1048576,6000,1));q.dispatch('request');q.unknown('request')
        self.assertEqual([op for op,a,ro in api.calls],['limits','reserve','dispatch','unknown'])
        self.assertTrue(all(a['scope']=='one-reviewed-run' for op,a,ro in api.calls))
    def test_claim_is_bound_to_scope_and_fenced_token(self):
        api=Mock();api.call.return_value=None
        store=PostgresStore(api,'reviewed')
        self.assertIsNone(store.claim('worker',None,120))
        self.assertEqual(api.call.call_args.args[1]['scope'],'reviewed')
    def test_invalid_lease_never_connects(self):
        api=Mock()
        with self.assertRaises(ValueError): PostgresStore(api).claim('worker',None,0)
        api.call.assert_not_called()
