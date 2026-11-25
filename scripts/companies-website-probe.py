"""Website finder probe (EXP-035, owner 25.11.2026 «Yes, run it»): 40 cards without a website, 10 each from FR, DE,
NO, US; registries, Wikidata, then one web search (Tavily, else Exa, else Jina). Reads the base, writes nothing.
The JSON report (names, sites, proofs; no keys) goes to the path given; counts go to the log."""
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collectors.companies import website_finder as W  # noqa: E402
from collectors.companies.pg_store import connect_params  # noqa: E402
from collectors.source_registry.search import providers as P  # noqa: E402

SAMPLE_SQL = """with l as (select distinct company_id from companies.profile_claim where claim_ref = 'b4:level' and qa_status = 'APPROVED'),
s as (select c.id, c.name, c.country,
  (select p.claim_value #>> '{}' from companies.profile_claim p where p.company_id = c.id and p.qa_status = 'APPROVED'
     and p.claim_ref = 'b4:legal_name' limit 1) as legal_name,
  (select p.claim_value #>> '{}' from companies.profile_claim p where p.company_id = c.id and p.qa_status = 'APPROVED'
     and p.claim_ref = 'b4:city' limit 1) as city,
  (select jsonb_object_agg(i.scheme, i.value) from companies.company_identifier i where i.company_id = c.id) as ids,
  row_number() over (partition by c.country order by c.id) as rn
  from companies.company c join l on l.company_id = c.id
  where (c.website is null or c.website = '') and c.country in ('FR', 'DE', 'NO', 'US'))
select id, name, country, legal_name, city, ids from s where rn <= 10 order by country, id"""

used = {'tavily': 0, 'exa': 0, 'jina': 0, 'failed': []}


def exa(q):
    data = P._post('https://api.exa.ai/search', {'query': q, 'numResults': 5, 'type': 'auto'}, {'x-api-key': P._key('EXA.txt')})
    return [{'url': r.get('url'), 'title': r.get('title')} for r in data.get('results', [])]


def search(q):
    for name, fn in (('tavily', lambda: P.tavily_api({'query': q, 'max_results': 5})['results']),
                     ('exa', lambda: exa(q)), ('jina', lambda: P.jina_search(q)['results'])):
        try:
            res = fn()
            used[name] += 1
            if res:
                return res
        except Exception as e:  # noqa: BLE001 — the next provider tries
            used['failed'].append(f'{name}: {type(e).__name__} {getattr(e, "code", "")}'.strip())
    return []


def main():
    import psycopg
    from psycopg.rows import dict_row
    with psycopg.connect(**connect_params(), row_factory=dict_row, autocommit=True) as conn:
        rows = conn.execute(SAMPLE_SQL).fetchall()
    out = []
    for c in rows:
        t = time.time()
        r = W.find(dict(c), search=search)
        r.update(id=c['id'], name=c['name'], country=c['country'], city=c['city'], secs=round(time.time() - t, 1))
        out.append(r)
        print(c['country'], (c['name'] or '')[:40].ljust(40), '->', r['website'] or '-', '|', r.get('via') or '', flush=True)
    found = [r for r in out if r['website']]
    summary = {'sample': len(out), 'found': len(found),
               'by_via': {v: sum(1 for r in found if r['via'] == v) for v in ('register', 'wikidata', 'search')},
               'by_country': {k: sum(1 for r in found if r['country'] == k) for k in ('FR', 'DE', 'NO', 'US')},
               'searches': {k: used[k] for k in ('tavily', 'exa', 'jina')}, 'search_failures': used['failed'][:20]}
    print(json.dumps(summary, indent=1))
    Path(sys.argv[1] if len(sys.argv) > 1 else 'website-probe.json').write_text(
        json.dumps({'summary': summary, 'companies': out}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
