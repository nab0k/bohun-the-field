#!/usr/bin/env python3
"""Russian defence-industry enterprises from the OFAC and UK sanctions lists — companies and cities only
(owner 23.11.2026, «А: оборонные + модель»; EXP-033 DESIGN_RU_DEFENCE_ENTERPRISES_v0.md).

Downloads the public lists, keeps legal entities with an address in Russia, takes those the list text calls
military, asks the free model chain about the industrial-sounding rest, and with --write stores the defence ones
in companies.ru_enterprise. Counts only in the log."""
import argparse
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.companies import products as P  # noqa: E402
from collectors.companies.autofill import model_chain  # noqa: E402
from collectors.companies.pg_store import connect_params  # noqa: E402
from collectors.companies.sources import ru_sanctions as R  # noqa: E402


def get(url, enc):
    req = urllib.request.Request(url, headers={'User-Agent': 'BOHUN-research/1.0'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode(enc, 'replace')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', type=int, default=800, help='ask the model about up to N industrial-sounding names')
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args()
    recs = R.ofac(get(R.OFAC_SDN, 'latin-1'), get(R.OFAC_ADD, 'latin-1')) + R.uk(get(R.UK_XML, 'utf-8'))
    merged = R.merge(recs)
    for e in merged:
        e['city'] = R.city(e['city'])
    sure = [dict(e, basis='list_text', answer=None) for e in merged if e['defence']]
    maybe = [e for e in merged if not e['defence'] and R.INDUSTRIAL.search(f"{e['name']} {e.get('name_ru') or ''}")][:a.model]
    chain = model_chain(os.environ)
    answers = {}
    if chain and maybe:
        def ask(text):
            last = None
            for p in chain:
                try:
                    return P.ask(text, p)
                except Exception as e:  # noqa: BLE001
                    last = e
            raise last
        answers = R.ask_industrial(maybe, ask)
    found = [dict(maybe[i], basis='model', answer='yes') for i, d in answers.items() if d == 'yes']
    keep = sure + found
    report = {'entities_in_russia': len(merged), 'defence_by_list_text': len(sure), 'industrial_asked': len(maybe),
              'model_answers': dict(Counter(answers.values())), 'defence_total': len(keep),
              'with_inn': sum(1 for e in keep if e.get('inn')), 'cities': len({e['city'] for e in keep if e['city']}),
              'write': a.write}
    if a.write and keep:
        import psycopg
        with psycopg.connect(**connect_params(), autocommit=True) as conn, conn.cursor() as cur:
            cur.executemany(R.UPSERT_SQL, [{'key': R.key(e), 'name': e['name'], 'name_ru': e.get('name_ru'), 'inn': e.get('inn'),
                                            'ogrn': e.get('ogrn'), 'city': e.get('city'), 'sector': e.get('sector'),
                                            'basis': e['basis'], 'answer': e.get('answer'),
                                            'sources': json.dumps(e['sources'])} for e in keep])
    text = json.dumps(report, indent=1)
    print(text)
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as f:
            f.write('## Russian defence-industry enterprises (sanctions lists)\n\n```\n' + text + '\n```\n')


if __name__ == '__main__':
    main()
