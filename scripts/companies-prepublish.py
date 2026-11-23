#!/usr/bin/env python3
"""Companies of the newest materials, before the site feed is refreshed (B4CE-04; owner 23.11.2026
«тексты сразу с компаниями и заполненными карточками», D-20261123-COMPANY-CARDS-AUTOPUBLISH).

Runs inside kb-daily after the relation extraction and before the site feed refresh:
1. the unsettled company mentions of the last HOURS hours -> rules, the model for the unclear ones, new candidate
   companies and the mention links (as the nightly discovery, on a small window);
2. the companies those materials name that have no card -> the autofill, now, at most LIMIT companies;
3. EDF project details for the new cards;
4. the site export (cards changed lately + the companies from the news) to OUT for the dispatch.
Counts only in the log. Every step is bounded in time; a failing step never stops the feed.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collectors.companies import autofill as A  # noqa: E402
from collectors.companies import discovery as X  # noqa: E402
from collectors.companies import products as P  # noqa: E402
from collectors.companies.pg_store import connect_params  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hours', type=int, default=36)
    ap.add_argument('--model', type=int, default=80, help="the model for up to N unclear names")
    ap.add_argument('--limit', type=int, default=12, help='autofill at most N companies')
    ap.add_argument('--minutes', type=int, default=15, help='time for the autofill')
    ap.add_argument('--out', default='cards.b64')
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args()
    import psycopg
    from psycopg.rows import dict_row
    report, started = {'write': a.write}, time.monotonic()
    run = 'prepublish ' + datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    with psycopg.connect(**connect_params(), row_factory=dict_row, autocommit=True) as conn:
        # 1. new companies from the newest mentions
        try:
            mentions = conn.execute(X.RECENT_MENTIONS_SQL, {'hours': a.hours}).fetchall()
            companies = conn.execute(X.COMPANIES_SQL).fetchall()
            decisions = X.plan(mentions, companies)
            chain = A.model_chain(os.environ)
            if a.model and chain and mentions:
                def ask(text):
                    last = None
                    for p in chain:
                        try:
                            return P.ask(text, p)
                        except Exception as e:  # noqa: BLE001
                            last = e
                    raise last
                report['model'] = X.classify_unsure(decisions, ask, {m['id']: m.get('context') or '' for m in mentions},
                                                    limit=a.model, log=lambda m: None)
            report['mentions'] = len(mentions)
            if a.write and decisions:
                with conn.cursor() as cur:
                    cur.executemany(X.REVIEW_SQL, [dict(d, run=run, model_country=d.get('model_country') or None)
                                                   for d in decisions])
                report['discovery'] = X.write(conn, decisions, run, conn.execute(X.COMPANIES_SQL).fetchall(),
                                              log=lambda m: None)
        except Exception as e:  # noqa: BLE001
            report['discovery_error'] = type(e).__name__
        # 2. cards for the companies the new materials name
        try:
            ids = [r['company_id'] for r in conn.execute(X.RECENT_COMPANIES_SQL, {'hours': a.hours}).fetchall()]
            report['named_without_card'] = len(ids)
            if ids:
                s = A.run(conn, os.environ, limit=a.limit, write=a.write, minutes=a.minutes, log=lambda *m: None, only=ids)
                report['autofill'] = {'chosen': s.get('chosen'), 'outcomes': s.get('outcomes')}
        except Exception as e:  # noqa: BLE001
            report['autofill_error'] = type(e).__name__
    # 3 and 4. EDF details, export for the site
    py = sys.executable
    if a.write:
        r = subprocess.run([py, str(ROOT / 'scripts/companies-edf-details.py'), '--write'], capture_output=True, text=True)
        report['edf'] = (r.stdout.strip().splitlines() or ['failed'])[-1][:200]
    r = subprocess.run([py, str(ROOT / 'scripts/company-cards-export.py'), a.out], capture_output=True, text=True)
    report['export'] = (r.stdout.strip().splitlines() or ['failed'])[-1][:200]
    report['seconds'] = round(time.monotonic() - started)
    text = json.dumps(report, indent=1)
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('## Companies before publication\n\n```\n' + text + '\n```\n')


if __name__ == '__main__':
    main()
