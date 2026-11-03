"""Cloud-only orchestration. Import/verify is stdlib-only and performs no IO writes."""
import ast
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[2]
HERE=Path(__file__).resolve().parent
CONFIRM='SYNTHETIC_POSTGRES_ONLY'
CONTAINER_PREFIX='bohun-exp016-pg-'


def require(ok,code):
    if not ok:raise RuntimeError(code)


def cloud_guard(env=None):
    if env is None:require(sys.platform=='linux','LINUX_RUNTIME_REQUIRED')
    env=os.environ if env is None else env
    require(env.get('GITHUB_ACTIONS')=='true' and env.get('RUNNER_OS')=='Linux','CLOUD_ONLY')
    require(env.get('GITHUB_REPOSITORY')=='nab0k/bohun-the-field'
            and env.get('EXP016_PUBLIC_REPO')=='true','PUBLIC_REPOSITORY_REQUIRED')
    require(env.get('EXP016_CONFIRMATION')==CONFIRM and env.get('GITHUB_EVENT_NAME')=='workflow_dispatch',
            'MANUAL_CONFIRMATION_REQUIRED')
    sha=env.get('EXP016_EXPECTED_SHA','')
    require(bool(re.fullmatch('[0-9a-f]{40}',sha)) and sha==env.get('GITHUB_SHA')
            and sha==env.get('GITHUB_WORKFLOW_SHA'),'APPROVED_SHA_REQUIRED')
    require(not any(k.startswith(('SUPABASE_','NOTION_','CLOUDFLARE_','GEMINI_','OPENAI_'))
                    for k in env),'WORKING_CREDENTIAL_ENV_DENIED')


def load_lock():
    return json.loads((HERE/'environment.lock.json').read_text())


def verify():
    lock=load_lock();require(lock.get('ready') is True,'UNRESOLVED_PINS')
    require(re.fullmatch(r'postgres@sha256:[0-9a-f]{64}',lock['postgres']['image']) is not None,'IMAGE_PIN_REQUIRED')
    require({p['name'] for p in lock['packages']}=={'psycopg','psycopg-binary'},'PACKAGE_SCOPE_CHANGED')
    for p in lock['packages']:
        require(p['version']=='3.3.6' and re.fullmatch('[0-9a-f]{64}',p['sha256']),'PACKAGE_PIN_REQUIRED')
    manifest=json.loads((HERE/'fixtures.json').read_text())['stage_hashes']
    require(len(manifest)==35,'FILE_MANIFEST_REQUIRED')
    for rel,h in manifest.items():
        p=(ROOT/rel).resolve()
        require(p.is_relative_to(ROOT) and re.fullmatch('[0-9a-f]{64}',h),'FILE_MANIFEST_INVALID')
        require(hashlib.sha256(p.read_bytes()).hexdigest()==h,'SOURCE_HASH_CHANGED')
        if p.suffix=='.py':ast.parse(p.read_text(),filename=str(p))
    return lock


def state_path():
    cloud_guard()
    tmp=Path(os.environ['RUNNER_TEMP']).resolve()
    require(tmp.is_absolute() and tmp!=ROOT and ROOT not in tmp.parents,'RUNNER_TEMP_REQUIRED')
    return tmp/'exp016-postgres-proof-state.json'


def command(args,*,env=None,input_text=None):
    # No shell; failing commands are reduced to a bounded code, not raw stderr/DSN.
    r=subprocess.run(args,env=env,input=input_text,text=True,capture_output=True,timeout=90)
    require(r.returncode==0,'COMMAND_FAILED_'+Path(args[0]).name.upper())
    return r.stdout.strip()


def prepare():
    cloud_guard();lock=verify();require(platform.python_version()==lock['python'],'PYTHON_VERSION_CHANGED')
    require(platform.machine()=='x86_64','LINUX_AMD64_REQUIRED')
    sp=state_path();require(not sp.exists(),'NO_AUTOMATIC_RESTART')
    # No network call before cloud guard and exact file-manifest verification.
    manifest=json.loads((ROOT/'experiments/exp016_backend_proof/fixtures.json').read_text())['stage_hashes']
    for rel,h in manifest.items():require(hashlib.sha256((ROOT/rel).read_bytes()).hexdigest()==h,'SOURCE_HASH_CHANGED')
    pw=secrets.token_hex(24);worker_pw=secrets.token_hex(24)
    print('::add-mask::'+pw);print('::add-mask::'+worker_pw)
    name=CONTAINER_PREFIX+secrets.token_hex(6)
    state={'container':name,'admin_password':pw,'worker_password':worker_pw,'phase':'STARTING'}
    sp.write_text(json.dumps(state));sp.chmod(0o600)
    # Requirements live only on the ephemeral cloud runner.
    requirements=sp.with_suffix('.requirements')
    requirements.write_text(''.join(p['name']+'=='+p['version']+' --hash=sha256:'+p['sha256']+'\n' for p in lock['packages']))
    command([sys.executable,'-m','pip','install','--no-deps','--only-binary=:all:','--require-hashes',
             '--disable-pip-version-check','-r',str(requirements)])
    command(['docker','pull','--platform=linux/amd64',lock['postgres']['image']])
    env=dict(os.environ,POSTGRES_PASSWORD=pw)
    command(['docker','run','--detach','--name',name,'--label','bohun.exp016=synthetic-proof',
             '--platform=linux/amd64','--publish','127.0.0.1::5432','--env','POSTGRES_PASSWORD',
             '--env','POSTGRES_DB=exp016_proof','--tmpfs','/var/lib/postgresql/data:rw,size=256m',
             lock['postgres']['image']],env=env)
    port=command(['docker','port',name,'5432/tcp'])
    require(bool(re.fullmatch(r'127\.0\.0\.1:[0-9]{4,5}',port)),'PUBLIC_BINDING_DENIED')
    state.update(port=int(port.split(':')[1]),phase='STARTED');sp.write_text(json.dumps(state))
    import psycopg
    for attempt in range(30):
        try:
            with psycopg.connect(host='127.0.0.1',port=state['port'],dbname='exp016_proof',
                 user='postgres',password=pw,connect_timeout=1,autocommit=True) as conn:
                require(conn.execute('SHOW server_version_num').fetchone()[0].startswith('17'),'POSTGRES_MAJOR_CHANGED')
            break
        except psycopg.OperationalError:
            if attempt==29:raise RuntimeError('DATABASE_NOT_READY') from None
            time.sleep(1)
    state['phase']='READY';sp.write_text(json.dumps(state))
    return state


def live():
    cloud_guard();verify();state=json.loads(state_path().read_text());require(state['phase']=='READY','PREPARE_REQUIRED')
    sys.path.insert(0,str(ROOT))
    suite=unittest.defaultTestLoader.loadTestsFromName('experiments.exp016_backend_proof.test_postgres_live')
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    require(result.testsRun>0 and not result.skipped and result.wasSuccessful(),'POSTGRES_PROOF_FAILED')
    state.update(phase='TESTS_PASSED',tests=result.testsRun);state_path().write_text(json.dumps(state))


def cleanup():
    cloud_guard();sp=state_path()
    if not sp.exists():return {'cleanup':'NO_CONTAINER_CREATED'}
    state=json.loads(sp.read_text());name=state['container']
    require(bool(re.fullmatch(CONTAINER_PREFIX+r'[0-9a-f]{12}',name)),'FOREIGN_CONTAINER_DENIED')
    # The random name and label together protect unrelated containers.
    check=subprocess.run(['docker','inspect','--format','{{index .Config.Labels "bohun.exp016"}}',name],
                         capture_output=True,text=True,timeout=15)
    if check.returncode==0:
        require(check.stdout.strip()=='synthetic-proof','FOREIGN_CONTAINER_DENIED')
        command(['docker','rm','--force','--volumes',name])
    elif state['phase']!='STARTING':raise RuntimeError('CLEANUP_NOT_CONFIRMED')
    summary={'phase':state['phase'],'tests':state.get('tests',0),'cleanup':'REMOVED',
             'sha':os.environ['EXP016_EXPECTED_SHA'],'image':load_lock()['postgres']['image']}
    # Remove credentials even when tests failed. Only bounded technical summary survives.
    sp.write_text(json.dumps(summary));sp.with_suffix('.requirements').unlink(missing_ok=True)
    print(json.dumps(summary,sort_keys=True))
    return summary


if __name__=='__main__':
    action=sys.argv[1] if len(sys.argv)==2 else ''
    try:
        require(action in ('verify','prepare','live','cleanup'),'ACTION_DENIED')
        if action=='verify':verify();print('OFFLINE_SOURCE_AND_PINS_OK')
        else:globals()[action]()
    except Exception as exc:
        # Do not print tracebacks from database/driver commands with connection details.
        code=str(exc) if type(exc) is RuntimeError and re.fullmatch('[A-Z0-9_]+',str(exc)) else 'PROOF_OPERATION_FAILED'
        print(code,file=sys.stderr);sys.exit(1)
