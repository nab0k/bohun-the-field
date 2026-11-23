#!/usr/bin/env python3
"""New companies from the knowledge base mentions — dry run (B4CE-04, EXP-033).

Reads knowledge.node (company / org) and companies.company, sorts the mentions by the code rules and, with
--model N, asks the model about up to N 'unsure' names. Prints counts only: this repository is public and the
mentions are unreviewed names from news. With --review the decisions go to the private table
companies.discovery_review (RLS on, no public access) for checking; no company record is written.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.companies import discovery as X  # noqa: E402
from collectors.companies import products as P  # noqa: E402
from collectors.companies.autofill import gemini_provider  # noqa: E402
from collectors.companies.pg_store import connect_params  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', type=int, default=0, help="ask the model about up to N 'unsure' names")
    ap.add_argument('--review', action='store_true', help='store the decisions in companies.discovery_review')
    ap.add_argument('--from-review', default='', help='take the decisions of this stored review run (no model calls)')
    ap.add_argument('--write', action='store_true', help='create the new candidate companies and the mention links')
    a = ap.parse_args()
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(**connect_params(), row_factory=dict_row, autocommit=True) as conn:
        mentions = conn.execute(X.MENTIONS_SQL).fetchall()
        companies = conn.execute(X.COMPANIES_SQL).fetchall()
        stored = conn.execute(X.REVIEW_ROWS_SQL, {'run': a.from_review}).fetchall() if a.from_review else []
    if a.from_review and not stored:
        sys.exit('no rows for that review run')
    decisions = [dict(r) for r in stored] if stored else X.plan(mentions, companies)
    before = X.summary(decisions)
    model = {}
    if a.model and not stored:
        chain = [p for p in [gemini_provider(os.environ)] + P.providers() if p]
        if not chain:
            sys.exit('no model key')
        used = {}

        def ask(text):
            """The first provider that answers; on an error the batch goes to the next one. Logs the provider
            name and the HTTP code only."""
            last = None
            for p in chain:
                try:
                    out = P.ask(text, p)
                    used[p[0] + ':' + p[3]] = used.get(p[0] + ':' + p[3], 0) + 1
                    return out
                except Exception as e:  # noqa: BLE001
                    last = e
                    print(f'model {p[0]}: {getattr(e, "code", type(e).__name__)}')
            raise last
        model = X.classify_unsure(decisions, ask, {m['id']: m.get('context') or '' for m in mentions}, limit=a.model)
        model['providers'] = used
    after = X.summary(decisions)
    if a.review:
        run = 'discovery dry run ' + datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
        with psycopg.connect(**connect_params(), autocommit=False) as conn, conn.cursor() as cur:
            cur.executemany(X.REVIEW_SQL, [dict(d, run=run, model_country=d.get('model_country') or None)
                                           for d in decisions])
            conn.commit()
        print(f'review rows: {len(decisions)} ({run})')
    report = {'rules': before, 'model': model, 'after_model': after}
    if a.write:
        run = 'discovery write ' + datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
        with psycopg.connect(**connect_params(), row_factory=dict_row, autocommit=True) as conn:
            report['write'] = X.write(conn, decisions, run, conn.execute(X.COMPANIES_SQL).fetchall(), log=lambda m: None)
    else:
        links, new = X.write_plan(decisions)
        report['would_write'] = {'new_names': len(new), 'links': len(links)}
    print(json.dumps(report, indent=1))
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('## Company discovery (dry run)\n\n```\n' + json.dumps(report, indent=1) + '\n```\n')


if __name__ == '__main__':
    main()
