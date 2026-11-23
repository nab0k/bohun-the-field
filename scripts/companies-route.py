#!/usr/bin/env python3
"""LEI, Wikidata QID and country for the companies without a register route (B4CE-04, EXP-033).

Dry run by default; --write stores the identifiers, a missing country and one route_attempt row per company.
The log shows counts only (public repository)."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.companies import route  # noqa: E402
from collectors.companies.pg_store import connect_params  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=300)
    ap.add_argument('--minutes', type=int, default=50)
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args()
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(**connect_params(), row_factory=dict_row, autocommit=True) as conn:
        summary = route.run(conn, write=a.write, limit=a.limit, minutes=a.minutes)
    text = json.dumps({**summary, 'write': a.write}, indent=1)
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('## Company route (Wikidata + GLEIF)\n\n```\n' + text + '\n```\n')


if __name__ == '__main__':
    main()
