#!/usr/bin/env python3
"""Company worker: one short pass of all the company work, started every 20 minutes (owner 23.11.2026: «нам не
нужна привязка к ночам или дням, нужно просто постоянно работать … сначала крупные компании, но параллельно — всё,
что в новостях и сигналах, синтезах»).

One pass, inside --minutes:
1. new companies from the mentions not settled yet (rules, the free model chain for up to --model unclear names);
2. LEI / Wikidata for companies without a register route (route step, a slice);
3. cards: the queue of refresh.py — named in the news / signals / syntheses after the last check first (larger
   first), then the large companies, then the overdue by status; carded ones in refresh mode, candidates with a
   route through the autofill;
4. EDF project details; then, if anything changed, the cards and the news companies go to the site (published at
   once, D-20261123-COMPANY-CARDS-AUTOPUBLISH);
5. the daily metric.
A failing step never stops the next one. Counts only in the log."""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collectors.companies import autofill as A  # noqa: E402
from collectors.companies import discovery as X  # noqa: E402
from collectors.companies import products as P  # noqa: E402
from collectors.companies import refresh as F  # noqa: E402
from collectors.companies import route as Rt  # noqa: E402
from collectors.companies.pg_store import connect_params  # noqa: E402


def chain_ask(chain):
    def ask(text):
        last = None
        for p in chain:
            try:
                return P.ask(text, p)
            except Exception as e:  # noqa: BLE001
                last = e
        raise last
    return ask


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--minutes', type=int, default=15)
    ap.add_argument('--model', type=int, default=40)
    ap.add_argument('--route', type=int, default=25)
    ap.add_argument('--out', default='cards.b64')
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args()
    import psycopg
    from psycopg.rows import dict_row
    start, today = time.monotonic(), date.today()
    left = lambda: a.minutes - (time.monotonic() - start) / 60  # noqa: E731
    report, changed = {'write': a.write}, False
    run = 'worker ' + datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%MZ')
    chain = A.model_chain(os.environ)
    quiet = lambda *m: None  # noqa: E731
    with psycopg.connect(**connect_params(), row_factory=dict_row, autocommit=True) as conn:
        try:  # 1. new companies
            mentions = conn.execute(X.NEW_MENTIONS_SQL).fetchall()[:400]
            decisions = X.plan(mentions, conn.execute(X.COMPANIES_SQL).fetchall())
            if a.model and chain and mentions:
                report['model'] = X.classify_unsure(decisions, chain_ask(chain), {m['id']: m.get('context') or '' for m in mentions},
                                                    limit=a.model, log=quiet)
            if a.write and decisions:
                with conn.cursor() as cur:
                    cur.executemany(X.REVIEW_SQL, [dict(d, run=run, model_country=d.get('model_country') or None)
                                                   for d in decisions])
                w = X.write(conn, decisions, run, conn.execute(X.COMPANIES_SQL).fetchall(), log=quiet)
                report['discovery'] = w
                changed = changed or bool(w.get('created'))
        except Exception as e:  # noqa: BLE001
            report['discovery_error'] = type(e).__name__
        try:  # 2. routes
            report['route'] = Rt.run(conn, write=a.write, limit=a.route, minutes=max(1, min(4, left() * 0.25)))['outcomes']
        except Exception as e:  # noqa: BLE001
            report['route_error'] = type(e).__name__
        try:  # 3. cards
            rows = conn.execute(F.DUE_SQL).fetchall()
            refresh_ids, fill_ids = F.plan(rows, 400, today)
            report['due'] = {'refresh': len(refresh_ids), 'fill': len(fill_ids)}
            budget = max(1.0, left() - 3)  # keep 3 minutes for EDF and the site
            # candidates (the news ones lead the list) get most of the pass; carded ones the rest
            fill_min = budget * (0.7 if refresh_ids else 1.0)
            if fill_ids:
                s = A.run(conn, os.environ, limit=len(fill_ids), write=a.write, minutes=max(1, fill_min),
                          log=quiet, only=fill_ids)
                report['fill'] = s.get('outcomes')
                changed = changed or bool((s.get('outcomes') or {}).get('written'))
            if refresh_ids and left() > 4:
                s = A.run(conn, os.environ, limit=len(refresh_ids), write=a.write, minutes=max(1, left() - 3),
                          log=quiet, only=refresh_ids, refresh=True)
                report['refresh'] = s.get('outcomes')
                changed = changed or bool((s.get('outcomes') or {}).get('refreshed'))
        except Exception as e:  # noqa: BLE001
            report['cards_error'] = type(e).__name__
        try:  # 5. metric
            m = F.metric(conn.execute(F.DUE_SQL).fetchall(), today)
            report['on_time_share'] = round(m['on_time'] / m['total'], 3) if m['total'] else None
            if a.write:
                conn.execute(F.METRIC_SQL, {**m, 'tiers': json.dumps(m['tiers'])})
        except Exception as e:  # noqa: BLE001
            report['metric_error'] = type(e).__name__
    py = sys.executable
    if a.write:  # 4. EDF details, then the export when something changed
        r = subprocess.run([py, str(ROOT / 'scripts/companies-edf-details.py'), '--write'], capture_output=True, text=True)
        edf = (r.stdout.strip().splitlines() or ['{}'])[-1]
        report['edf'] = edf[:160]
        try:
            changed = changed or json.loads(edf).get('claims_enriched', 0) > 0
        except ValueError:
            pass
    if changed:
        r = subprocess.run([py, str(ROOT / 'scripts/company-cards-export.py'), a.out], capture_output=True, text=True)
        report['export'] = (r.stdout.strip().splitlines() or ['failed'])[-1][:200]
    elif Path(a.out).exists():
        Path(a.out).unlink()
    report['minutes'] = round((time.monotonic() - start) / 60, 1)
    text = json.dumps(report, indent=1)
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('## Company worker\n\n```\n' + text + '\n```\n')


if __name__ == '__main__':
    main()
