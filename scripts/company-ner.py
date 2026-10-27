#!/usr/bin/env python3
"""One bounded persistent NER tick. No implicit install, migration or schedule."""
import argparse
import json
import os
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
PHASE = 'startup'


def safe_failure(exc, phase):
    """Emit enums/SQLSTATE only. Never log raw messages, DSNs or source rows."""
    phases = {'startup','dependencies','configuration','runtime_paths','worker',
              'database_connect','database_transaction'}
    phase = phase if phase in phases else 'unknown'
    state = getattr(exc, 'sqlstate', None)
    if not isinstance(state, str) or not re.fullmatch('[0-9A-Z]{5}', state):
        state = None
    message = str(exc).lower()  # inspect in memory; no fragments enter output
    if 'certificate verify failed' in message or 'certificate verification failed' in message:
        category = 'TLS_CERTIFICATE'
    elif 'password authentication failed' in message:
        category = 'AUTHENTICATION'
    elif 'could not translate host name' in message or 'name or service not known' in message:
        category = 'DNS'
    elif state == '42501':
        category = 'DATABASE_PERMISSION'
    elif state in {'42P01','42703','3F000'}:
        category = 'DATABASE_SCHEMA'
    elif isinstance(exc, TimeoutError) or 'timed out' in message or 'timeout expired' in message:
        category = 'TIMEOUT'
    elif isinstance(exc, ImportError):
        category = 'DEPENDENCY'
    elif isinstance(exc, FileNotFoundError):
        category = 'MISSING_RUNTIME_FILE'
    elif isinstance(exc, ValueError):
        category = 'INVALID_CONFIGURATION_OR_INPUT'
    else:
        category = 'UNKNOWN'
    names = {'OperationalError','ProgrammingError','InsufficientPrivilege','UndefinedTable',
             'UndefinedColumn','ModuleNotFoundError','ImportError','TimeoutError',
             'FileNotFoundError','ValueError','RuntimeError','DatabaseUnknown'}
    name = type(exc).__name__
    return {'event':'COMPANY_NER_STOP','phase':phase,'category':category,
            'exception_type':name if name in names else 'Exception','sqlstate':state,
            'automatic_retry':False}


def main():
    global PHASE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Permit this invocation to write private checkpoints')
    parser.add_argument('--dsn-env', default='COMPANY_NER_DATABASE_URL', help='Name only; value is never logged')
    parser.add_argument('--model-python', required=True, help='Existing Linux Python3.13 with pinned packages')
    parser.add_argument('--model-dir', type=Path, required=True, help='Already acquired pinned model; no download')
    parser.add_argument('--limit', type=int, default=12, choices=range(1, 51))
    args = parser.parse_args()
    if not args.run:
        parser.error('--run required; no connections or inference performed')
    PHASE = 'dependencies'
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    from collectors.company_ner.native import Native
    from collectors.company_ner.store import Store
    from collectors.company_ner.worker import run
    PHASE = 'configuration'
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        raise ValueError('NER_DATABASE_SECRET_ABSENT')
    params = conninfo_to_dict(dsn)
    if params.get('sslmode') != 'verify-full' or not params.get('sslrootcert'):
        raise ValueError('NER_VERIFIED_TLS_REQUIRED')
    def connect():
        global PHASE
        PHASE = 'database_connect'
        conn = psycopg.connect(dsn, autocommit=False, row_factory=dict_row, connect_timeout=10)
        PHASE = 'database_transaction'
        return conn
    store = Store(connect)
    PHASE = 'runtime_paths'
    native = Native(args.model_python, args.model_dir)
    PHASE = 'worker'
    result = run(store, native, ingest_limit=args.limit,
                 model_limit=args.limit, link_limit=args.limit)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(safe_failure(exc, PHASE), sort_keys=True), file=sys.stderr)
        raise SystemExit('COMPANY_NER_STOP_NO_AUTOMATIC_RETRY') from None
