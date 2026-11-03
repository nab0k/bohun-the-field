"""Fixed-RPC, one-transaction transport. Driver imported only by cloud_connect."""
from dataclasses import dataclass,field
import re

from collectors.companies.automation import checkpoint as wire, contracts as c
from collectors.companies.automation.postgres import BackendFault
from collectors.companies.automation.worker import UnknownCommit

READ_ONLY = frozenset(('status','read_current','read_registry_page','lookup_result','lookup_batch'))
ACTIONS = READ_ONLY | frozenset(('claim','context','read_prepared','save_prepared','commit_result',
                               'renew','defer','commit_batch','plan_page'))
SQL = 'SELECT companies.automation_rpc_v0(%s, %s::jsonb)'


@dataclass(frozen=True)
class Endpoint:
    host: str
    port: int
    database: str
    user: str
    password: str=field(repr=False)

    def __post_init__(self):
        c.require(self.host=='127.0.0.1' and type(self.port) is int and 1024<=self.port<=65535,
                  'DISPOSABLE_ENDPOINT_REQUIRED')
        c.require(self.database=='exp016_proof' and self.user=='bohun_companies_worker_v0'
                  and type(self.password) is str and len(self.password)>=24,'TEST_CREDENTIALS_REQUIRED')


def cloud_connect(endpoint):
    # Deliberately no shared PgStore, conninfo/env parser, pooler, or service_role.
    from .environment import cloud_guard
    cloud_guard()
    c.require(type(endpoint) is Endpoint,'DISPOSABLE_ENDPOINT_REQUIRED')
    import psycopg
    return psycopg.connect(host=endpoint.host,port=endpoint.port,dbname=endpoint.database,
                          user=endpoint.user,password=endpoint.password,autocommit=True,
                          connect_timeout=5,application_name='exp016_disposable_proof',
                          options='-c statement_timeout=10000 -c lock_timeout=3000')


class RpcTransport:
    def __init__(self,connect,*,after_commit=None):
        c.require(callable(connect),'CONNECTION_FACTORY_REQUIRED')
        self.connect=connect;self.after_commit=after_commit;self.commits=0

    def __call__(self,action,request):
        c.require(action in ACTIONS,'ACTION_DENIED')
        request=wire.bounded(request)
        wire.shape(request,{'wire_version','policy_hash','args'})
        c.require(request['wire_version']==1 and type(request['wire_version']) is int
                  and c.is_hash(request['policy_hash']) and type(request['args']) is dict,'REQUEST_INVALID')
        conn=None;phase='CONNECT'
        try:
            conn=self.connect()
            phase='BEGIN'
            conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY' if action in READ_ONLY
                         else 'BEGIN ISOLATION LEVEL SERIALIZABLE')
            phase='RPC'
            row=conn.execute(SQL,(action,c.json_text(request))).fetchone()
            c.require(type(row) in (tuple,list) and len(row)==1,'RPC_ROW_INVALID')
            response=wire.bounded(row[0]);wire.shape(response,{'wire_version','status','data'})
            c.require(response['wire_version']==1 and response['status']=='OK','RPC_REPLY_INVALID')
            phase='COMMIT'
            conn.commit();self.commits+=1
            phase='COMMITTED'
            if self.after_commit:self.after_commit(action)
            return response
        except Exception as exc:
            # SQLSTATE class 40 explicitly means rollback, even if COMMIT was attempted.
            state=getattr(exc,'sqlstate',None)
            if conn is not None:
                try:conn.rollback()
                except Exception:pass
            if state in ('40001','40P01'):
                raise BackendFault('TX_SERIALIZATION_ROLLBACK' if state=='40001' else 'TX_DEADLOCK_ROLLBACK') from None
            if state=='P1600' and phase!='COMMITTED':
                detail=getattr(getattr(exc,'diag',None),'message_detail',None)
                if type(detail) is str and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',detail):
                    raise BackendFault(detail) from None
            if phase in ('COMMIT','COMMITTED'):
                raise UnknownCommit() from None
            if isinstance(exc,c.ContractError):raise
            raise BackendFault('TX_NOT_COMMITTED') from None
        finally:
            if conn is not None:
                try:conn.close()
                except Exception:pass
