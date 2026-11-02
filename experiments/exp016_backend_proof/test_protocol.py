"""Offline driver doubles. Does not import psycopg or execute SQL."""
import copy
from dataclasses import replace
import unittest
from unittest.mock import patch

from collectors.companies.automation import contracts as c
from collectors.companies.automation.postgres import BackendFault
from collectors.companies.automation.worker import UnknownCommit
from .rpc_transport import Endpoint,RpcTransport,SQL
from .environment import cloud_guard,CONFIRM,verify

REQUEST={'wire_version':1,'policy_hash':c.Policy().fingerprint,'args':{}}
RESPONSE={'wire_version':1,'status':'OK','data':None}


class DriverError(Exception):
    def __init__(self,state=None,detail=None):
        self.sqlstate=state
        self.diag=type('Diag',(),{'message_detail':detail})()
        super().__init__('PRIVATE password and host must never escape')


class Connection:
    def __init__(self,fail=None):
        self.calls=[];self.fail=fail;self.response=copy.deepcopy(RESPONSE)
    def execute(self,sql,args=None):
        self.calls.append((sql,args))
        if sql==SQL and self.fail:raise self.fail
        return self
    def fetchone(self):return (self.response,)
    def commit(self):self.calls.append(('commit',None))
    def rollback(self):self.calls.append(('rollback',None))
    def close(self):self.calls.append(('close',None))


def valid_env():
    return {'GITHUB_ACTIONS':'true','RUNNER_OS':'Linux','GITHUB_REPOSITORY':'nab0k/bohun-the-field',
      'EXP016_PUBLIC_REPO':'true','EXP016_CONFIRMATION':CONFIRM,'GITHUB_EVENT_NAME':'workflow_dispatch',
      'EXP016_EXPECTED_SHA':'a'*40,'GITHUB_SHA':'a'*40,'GITHUB_WORKFLOW_SHA':'a'*40}


class ProtocolTests(unittest.TestCase):
    def test_endpoint_cannot_select_production(self):
        good=Endpoint('127.0.0.1',12345,'exp016_proof','bohun_companies_worker_v0','x'*24)
        for kwargs in ({'host':'db.example.com'},{'host':'localhost'},{'database':'postgres'},
                       {'user':'postgres'},{'port':543},{'password':''}):
            with self.subTest(kwargs=kwargs),self.assertRaises(c.ContractError):replace(good,**kwargs)

    def test_cloud_guards_and_working_secrets(self):
        env=valid_env();cloud_guard(env)
        for k,v in {'GITHUB_ACTIONS':'false','EXP016_PUBLIC_REPO':'false','GITHUB_SHA':'b'*40,
                    'EXP016_CONFIRMATION':'yes','RUNNER_OS':'macOS','GITHUB_EVENT_NAME':'push',
                    'SUPABASE_DB_PASSWORD':'private'}.items():
            with self.subTest(key=k),self.assertRaises(RuntimeError):cloud_guard(env|{k:v})

    def test_transaction_success_returns_only_after_commit(self):
        conn=Connection();rpc=RpcTransport(lambda:conn)
        self.assertEqual(rpc('claim',REQUEST),RESPONSE)
        self.assertEqual([s for s,_ in conn.calls],['BEGIN ISOLATION LEVEL SERIALIZABLE',SQL,'commit','close'])
        self.assertEqual(rpc.commits,1)

    def test_snapshot_read_is_repeatable_read_only(self):
        conn=Connection();RpcTransport(lambda:conn)('read_current',REQUEST)
        self.assertEqual(conn.calls[0][0],'BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')

    def test_action_injection_is_denied_before_connection(self):
        with self.assertRaises(c.ContractError):RpcTransport(lambda:self.fail())('DROP TABLE company',REQUEST)

    def test_request_values_remain_bound_parameters(self):
        conn=Connection();request=copy.deepcopy(REQUEST);request['args']={'origin':"'); DROP TABLE fake; --"}
        RpcTransport(lambda:conn)('commit_batch',request)
        self.assertEqual(conn.calls[1],(SQL,('commit_batch',c.json_text(request))))

    def test_known_private_reject_is_bounded_and_rolled_back(self):
        conn=Connection(DriverError('P1600','SOURCE_ADMISSION_REQUIRED'))
        with self.assertRaisesRegex(BackendFault,'^SOURCE_ADMISSION_REQUIRED$'):
            RpcTransport(lambda:conn)('commit_result',REQUEST)
        self.assertNotIn(('commit',None),conn.calls);self.assertIn(('rollback',None),conn.calls)

    def test_serialization_and_deadlock_are_known_rollbacks(self):
        for state,code in [('40001','TX_SERIALIZATION_ROLLBACK'),('40P01','TX_DEADLOCK_ROLLBACK')]:
            conn=Connection(DriverError(state))
            with self.subTest(state=state),self.assertRaisesRegex(BackendFault,code):
                RpcTransport(lambda:conn)('commit_result',REQUEST)
            self.assertNotIn(('commit',None),conn.calls)

    def test_precommit_failure_does_not_print_private_exception(self):
        conn=Connection(DriverError())
        with self.assertRaisesRegex(BackendFault,'^TX_NOT_COMMITTED$'):RpcTransport(lambda:conn)('claim',REQUEST)
        self.assertEqual(conn.calls[-1][0],'close')

    def test_commit_connection_loss_is_unknown_not_rollback_proof(self):
        conn=Connection()
        def lost():raise DriverError()
        conn.commit=lost
        with self.assertRaises(UnknownCommit):RpcTransport(lambda:conn)('commit_result',REQUEST)

    def test_commit_serialization_error_is_known_rollback(self):
        conn=Connection()
        def aborted():raise DriverError('40001')
        conn.commit=aborted
        with self.assertRaisesRegex(BackendFault,'TX_SERIALIZATION_ROLLBACK'):
            RpcTransport(lambda:conn)('commit_result',REQUEST)

    def test_password_is_not_in_endpoint_representation(self):
        value='a_private_synthetic_password'
        self.assertNotIn(value,repr(Endpoint('127.0.0.1',12345,'exp016_proof','bohun_companies_worker_v0',value)))

    def test_lost_ack_after_committed_effect_is_unknown(self):
        conn=Connection()
        def lost(_):raise TimeoutError('private')
        rpc=RpcTransport(lambda:conn,after_commit=lost)
        with self.assertRaises(UnknownCommit):rpc('commit_result',REQUEST)
        self.assertEqual(rpc.commits,1);self.assertEqual([s for s,_ in conn.calls].count('commit'),1)

    def test_bad_response_rolls_back_before_commit(self):
        conn=Connection();conn.response={'bad':True}
        with self.assertRaises(c.ContractError):RpcTransport(lambda:conn)('claim',REQUEST)
        self.assertNotIn(('commit',None),conn.calls)

    def test_lock_and_local_ast_verify_without_install_or_process(self):
        with patch('subprocess.run',side_effect=AssertionError('no external process')):
            self.assertTrue(verify()['ready'])
