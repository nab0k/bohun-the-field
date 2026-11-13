"""Manual runs of the reviewed C2-10 descriptions batch. Public logs carry counters only."""
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from experiments.exp016_descriptions_import import batch as b  # noqa: E402

REPOSITORY = 'nab0k/bohun-the-field'
CONFIRM = {'dry_run': 'DESCRIPTIONS_DRY_RUN', 'write': 'DESCRIPTIONS_WRITE_1_BATCH', 'rollback': 'DESCRIPTIONS_ROLLBACK_BATCH'}
DENIED = ('NOTION_', 'CLOUDFLARE_', 'OPENAI_', 'GROQ_', 'MISTRAL_', 'INOREADER_', 'GEMINI_')
LOCK = ROOT / 'experiments/exp016_backend_proof/environment.lock.json'
SCHEMA = ROOT / 'db/companies/001_companies_schema.sql'
PROFILE_SCHEMA = ROOT / 'experiments/exp016_backend_proof/bootstrap.sql'
CONTAINER_LABEL = 'bohun.exp016=descriptions-rehearsal'
PACKET_PREFIX = 'EXP016_DESCRIPTIONS_PACKET_'


def guard(env=None, *, production=False):
    env = os.environ if env is None else env
    checks = [sys.platform == 'linux', env.get('GITHUB_ACTIONS') == 'true', env.get('GITHUB_REPOSITORY') == REPOSITORY,
              env.get('GITHUB_EVENT_NAME') == 'workflow_dispatch', env.get('GITHUB_REF') == 'refs/heads/main',
              re.fullmatch('[0-9a-f]{40}', env.get('EXPECTED_SHA', '')) is not None,
              env.get('EXPECTED_SHA') == env.get('GITHUB_SHA') == env.get('GITHUB_WORKFLOW_SHA'),
              CONFIRM.get(env.get('IMPORT_MODE')) == env.get('IMPORT_CONFIRMATION')]
    if not all(checks):
        raise b.BatchError('CLOUD_MANUAL_EXACT_SHA_ONLY')
    if any(k.startswith(DENIED) for k in env):
        raise b.BatchError('OTHER_CREDENTIAL_DENIED')
    if bool(env.get('SUPABASE_DB_PASSWORD')) != production:
        raise b.BatchError('DATABASE_PASSWORD_ONLY_IN_THE_PRODUCTION_STEP')


def load_plan(path=None):
    return json.loads(Path(path or HERE / 'plan.json').read_text())


def load_packet(env, expected_sha256):
    encoded = ''.join(env.get(PACKET_PREFIX + str(n), '') for n in range(1, 5))
    if not encoded.strip():
        raise b.BatchError('PACKET_MISSING')
    raw = gzip.decompress(base64.b64decode(encoded, validate=True))
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise b.BatchError('PACKET_PIN')
    return b.validate(json.loads(raw))


def now():
    return time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime())


def summary(values):
    text = '## EXP-016 C2-10 descriptions batch, counters only\n```json\n' + json.dumps(values, indent=1, sort_keys=True) + '\n```\n'
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write(text)


def install():
    lock = json.loads(LOCK.read_text())
    req = Path(os.environ['RUNNER_TEMP']) / 'exp016-descriptions.requirements'
    req.write_text(''.join(p['name'] + '==' + p['version'] + ' --hash=sha256:' + p['sha256'] + '\n' for p in lock['packages']))
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--no-deps', '--only-binary=:all:', '--require-hashes',
                    '--disable-pip-version-check', '-r', str(req)], check=True, capture_output=True, timeout=180)


def cycle(conn, packet):
    """Full cycle on a database that already has the schema. Used by the throwaway rehearsal and local tests."""
    at = now()
    for c in packet['companies']:
        conn.execute("INSERT INTO company(id,name,name_norm,status,created_at,updated_at) VALUES(%s,%s,%s,'candidate',%s,%s)",
                     (c['company_id'], c['company_id'], c['company_id'], at, at))
    # A neighbour that must survive: an older claim on one batch company with its own source.
    first = packet['companies'][0]['company_id']
    src = conn.execute("""INSERT INTO profile_source(company_id,source_ref,title,source_url,source_kind,as_of)
                          VALUES(%s,'s_other','Other','https://other.test/','company_website','2026-10-01') RETURNING id""", (first,)).fetchone()[0]
    claim = conn.execute("""INSERT INTO profile_claim(company_id,claim_ref,field_path,claim_value,assessment,content_sha256)
                            VALUES(%s,'c13','/summary/primary_activity','"other"'::jsonb,'company_claim',%s) RETURNING id""",
                         (first, '0' * 64)).fetchone()[0]
    conn.execute('INSERT INTO profile_claim_source(company_id,claim_id,source_id) VALUES(%s,%s,%s)', (first, claim, src))
    conn.commit()
    base = b.counts(conn)
    before = b.precheck(conn, packet)
    report = b.apply(conn, packet, at)
    b.verify(before, b.counts(conn), report, packet)
    statuses = conn.execute("""SELECT DISTINCT qa_status, public_release FROM profile_claim
                               WHERE claim_ref LIKE %s""", (b.CLAIM_PREFIX + '%',)).fetchall()
    if [tuple(x) for x in statuses] != [('HOLD', False)]:
        raise b.BatchError('CLAIMS_NOT_HOLD')
    conn.commit()
    try:
        b.precheck(conn, packet)
        raise b.BatchError('REPEAT_NOT_REFUSED')
    except b.BatchError as exc:
        if str(exc) != 'BATCH_ALREADY_PRESENT':
            raise
    undone = b.undo(conn, packet)
    conn.commit()
    if b.counts(conn) != base:
        raise b.BatchError('UNDO_CHANGED_THE_NEIGHBOURS')
    return {'report': report, 'undone': undone, 'restored': True}


def rehearse(packet):
    """The whole cycle on a throwaway database. No production credential exists here."""
    import psycopg
    image = json.loads(LOCK.read_text())['postgres']['image']
    name = 'bohun-exp016-descriptions-' + secrets.token_hex(6)
    password = secrets.token_hex(24)
    print('::add-mask::' + password)
    env = dict(os.environ, POSTGRES_PASSWORD=password)
    subprocess.run(['docker', 'pull', '--platform=linux/amd64', image], check=True, capture_output=True, timeout=300)
    subprocess.run(['docker', 'run', '--detach', '--name', name, '--label', CONTAINER_LABEL, '--platform=linux/amd64',
                    '--publish', '127.0.0.1::5432', '--env', 'POSTGRES_PASSWORD', '--env', 'POSTGRES_DB=exp016_descriptions',
                    '--tmpfs', '/var/lib/postgresql/data:rw,size=256m', image], check=True, capture_output=True, env=env, timeout=120)
    try:
        port = int(subprocess.run(['docker', 'port', name, '5432/tcp'], check=True, capture_output=True, text=True, timeout=30).stdout.strip().rsplit(':', 1)[1])
        params = dict(host='127.0.0.1', port=port, dbname='exp016_descriptions', user='postgres', password=password, connect_timeout=2)
        for attempt in range(40):
            try:
                psycopg.connect(**params).close()
                break
            except psycopg.OperationalError:
                if attempt == 39:
                    raise b.BatchError('DATABASE_NOT_READY') from None
                time.sleep(1)
        with psycopg.connect(**params) as conn:
            conn.execute(SCHEMA.read_text())
            conn.execute(PROFILE_SCHEMA.read_text())
            conn.execute('SET search_path TO companies')
            conn.commit()
            return cycle(conn, packet)
    finally:
        subprocess.run(['docker', 'rm', '--force', '--volumes', name], capture_output=True, timeout=60)


def production(packet, plan, mode):
    import psycopg
    from collectors.companies.pg_store import connect_params
    with psycopg.connect(**connect_params(), options='-c statement_timeout=300000 -c lock_timeout=10000') as conn:
        conn.execute('SET search_path TO companies')
        conn.commit()  # A later rollback must not undo the session setting.
        if mode == 'rollback':
            before = b.counts(conn)
            undone = b.undo(conn, packet)
            conn.commit()
            return {'mode': mode, 'before': before, 'undone': undone, 'after': b.counts(conn)}
        before = b.precheck(conn, packet)
        report = b.apply(conn, packet, now())
        b.verify(before, b.counts(conn), report, packet)
        if mode == 'dry_run':
            conn.rollback()
            return {'mode': mode, 'committed': False, 'report': report, 'after_rollback': b.counts(conn)}
        conn.commit()
        return {'mode': mode, 'committed': True, 'report': report, 'before': before, 'after': b.counts(conn)}


def main(argv=None):
    action = (argv or sys.argv[1:] or [''])[0]
    try:
        if action == 'install':
            guard()
            install()
            return 0
        if action == 'rehearse':
            guard()
            plan = load_plan()
            summary(rehearse(load_packet(os.environ, plan['packet_sha256'])))
            return 0
        if action == 'production':
            guard(production=True)
            plan = load_plan()
            summary(production(load_packet(os.environ, plan['packet_sha256']), plan, os.environ['IMPORT_MODE']))
            return 0
        raise b.BatchError('UNKNOWN_ACTION')
    except Exception as exc:  # A bounded code only: driver text may quote private rows or hosts.
        code = str(exc) if re.fullmatch('[A-Z][A-Z0-9_]{0,79}', str(exc)) else type(exc).__name__
        state = getattr(exc, 'sqlstate', None)
        print('IMPORT_FAILED:' + code + (':' + state if isinstance(state, str) and re.fullmatch('[0-9A-Z]{5}', state) else ''))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
