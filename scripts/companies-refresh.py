#!/usr/bin/env python3
"""Catalogue refresh by parts, nightly after the autofill (owner 23.11.2026 «Перепроверка каталога»).

Picks the due companies (collectors/companies/refresh.py: events first, then the most overdue against the period of
their status), re-checks carded ones in refresh mode and candidates with a route through the autofill, within a
budget and a time limit, and records the daily metric «share of the catalogue checked within its period».
Counts only in the log."""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.companies import autofill as A  # noqa: E402
from collectors.companies import refresh as F  # noqa: E402
from collectors.companies.pg_store import connect_params  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--budget', type=int, default=80)
    ap.add_argument('--minutes', type=int, default=25)
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args()
    import psycopg
    from psycopg.rows import dict_row
    today = date.today()
    with psycopg.connect(**connect_params(), row_factory=dict_row, autocommit=True) as conn:
        rows = conn.execute(F.DUE_SQL).fetchall()
        refresh_ids, fill_ids = F.plan(rows, a.budget, today)
        report = {'due': len(F.due(rows, today)), 'refresh': len(refresh_ids), 'fill': len(fill_ids), 'write': a.write}
        quiet = lambda *m: None  # noqa: E731
        if refresh_ids:
            s = A.run(conn, os.environ, limit=len(refresh_ids), write=a.write, minutes=a.minutes * 2 // 3, log=quiet,
                      only=refresh_ids, refresh=True)
            report['refresh_outcomes'] = s.get('outcomes')
        if fill_ids:
            s = A.run(conn, os.environ, limit=len(fill_ids), write=a.write, minutes=max(1, a.minutes // 3), log=quiet, scheduled=True,
                      only=fill_ids)
            report['fill_outcomes'] = s.get('outcomes')
        m = F.metric(conn.execute(F.DUE_SQL).fetchall(), today)
        report['metric'] = {k: m[k] for k in ('total', 'on_time', 'overdue', 'never', 'today')}
        report['on_time_share'] = round(m['on_time'] / m['total'], 3) if m['total'] else None
        if a.write:
            conn.execute(F.METRIC_SQL, {**m, 'tiers': json.dumps(m['tiers'])})
    text = json.dumps(report, indent=1)
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('## Catalogue refresh by parts\n\n```\n' + text + '\n```\n')


if __name__ == '__main__':
    main()
