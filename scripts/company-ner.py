#!/usr/bin/env python3
"""One bounded persistent NER tick. No implicit install, migration or schedule."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Permit this invocation to write private checkpoints')
    parser.add_argument('--dsn-env', default='COMPANY_NER_DATABASE_URL', help='Name only; value is never logged')
    parser.add_argument('--model-python', required=True, help='Existing Linux Python3.13 with pinned packages')
    parser.add_argument('--model-dir', type=Path, required=True, help='Already acquired pinned model; no download')
    parser.add_argument('--limit', type=int, default=12, choices=range(1, 51))
    args = parser.parse_args()
    if not args.run:
        parser.error('--run required; no connections or inference performed')
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row
    from collectors.company_ner.native import Native
    from collectors.company_ner.store import Store
    from collectors.company_ner.worker import run
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        raise ValueError('NER_DATABASE_SECRET_ABSENT')
    params = conninfo_to_dict(dsn)
    if params.get('sslmode') != 'verify-full' or not params.get('sslrootcert'):
        raise ValueError('NER_VERIFIED_TLS_REQUIRED')
    store = Store(lambda: psycopg.connect(dsn, autocommit=False, row_factory=dict_row, connect_timeout=10))
    result = run(store, Native(args.model_python, args.model_dir), ingest_limit=args.limit,
                 model_limit=args.limit, link_limit=args.limit)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # DB exceptions may include rows/DSNs. Only local/private tooling should
        # inspect details; cloud stdout must contain no source or secret values.
        raise SystemExit('COMPANY_NER_STOP_NO_AUTOMATIC_RETRY') from None
