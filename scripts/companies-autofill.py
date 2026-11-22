#!/usr/bin/env python3
"""Autonomous company cards, design B v1 slice 3 step 3.5 (B4CE-04, EXP-033).

Without --write nothing is stored (dry run). With --write the cards that pass the code rules go to the
schema `companies` with public_release=false; publication stays a separate, owner-approved step.
Secrets come only from the environment (SUPABASE_DB_PASSWORD, GEMINI_API_KEY) and are never printed.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.companies import autofill  # noqa: E402
from collectors.companies.pg_store import connect_params  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=40)
    ap.add_argument('--country', action='append', help='ISO code; repeat for several')
    ap.add_argument('--minutes', type=int, default=50, help='time budget for the card loop')
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(**connect_params(), row_factory=dict_row, autocommit=True) as conn:
        summary = autofill.run(conn, os.environ, limit=args.limit, countries=set(args.country or []) or None,
                               write=args.write, minutes=args.minutes)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    out = os.environ.get('GITHUB_STEP_SUMMARY')
    if out:
        with open(out, 'a', encoding='utf-8') as f:
            f.write(f"## Company cards {summary['run']}\n\nOutcomes: {summary['outcomes']} · claims {summary['claims']}\n\n")
            for title, rows in (('Written', summary['written']), ('Held for review', summary['held'])):
                if rows:
                    f.write(f'### {title}\n\n' + ''.join(f'- {r}\n' for r in rows) + '\n')


if __name__ == '__main__':
    main()
