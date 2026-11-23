#!/usr/bin/env python3
"""Add European Defence Fund project details to the approved programme claims of company cards (B4CE-04,
owner 23.11.2026; D-20261123-COMPANY-CARDS-AUTOPUBLISH). Only claims of kind edf_grant without details are
touched; the value gains an "edf" object (acronym, title, topic, EU contribution, the company's role and
contribution, dates, status, partners, coordinator, project page). Dry run by default; counts only in the log."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collectors.companies.pg_store import connect_params  # noqa: E402
from collectors.companies.sources import edf_projects as E  # noqa: E402

TODO_SQL = """select p.id, p.company_id, p.claim_value, c.name,
  (select claim_value #>> '{}' from companies.profile_claim l where l.company_id = p.company_id
     and l.claim_ref = 'b4:legal_name' and l.qa_status = 'APPROVED' limit 1) as legal_name,
  (select array_agg(value) from companies.company_identifier i where i.company_id = p.company_id and i.scheme = 'EU_PIC') as pics
from companies.profile_claim p join companies.company c on c.id = p.company_id
where p.qa_status = 'APPROVED' and p.claim_value->>'kind' = 'edf_grant' and not (p.claim_value ? 'edf')
order by p.id limit %(limit)s"""
UPDATE_SQL = """update companies.profile_claim set claim_value = claim_value || jsonb_build_object('edf', %(edf)s::jsonb),
  content_sha256 = encode(sha256(convert_to((claim_value || jsonb_build_object('edf', %(edf)s::jsonb))::text, 'UTF8')), 'hex'),
  review_note = review_note || ' | EDF project details %(day)s', updated_at = now()
where id = %(id)s and not (claim_value ? 'edf')"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=2000)
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args()
    import psycopg
    from datetime import date
    from psycopg.rows import dict_row
    with psycopg.connect(**connect_params(), row_factory=dict_row, autocommit=True) as conn:
        rows = conn.execute(TODO_SQL, {'limit': a.limit}).fetchall()
        details = E.fetch({r['claim_value']['ref'] for r in rows}) if rows else {}
        done = role = 0
        for r in rows:
            d = details.get(str(r['claim_value']['ref']))
            if not d:
                continue
            edf = E.for_company(d, r['pics'] or [], [r['name'], r['legal_name']])
            role += 'role' in edf
            done += 1
            if a.write:
                conn.execute(UPDATE_SQL.replace('%(day)s', date.today().isoformat()),
                             {'edf': json.dumps(edf, ensure_ascii=False), 'id': r['id']})
    print(json.dumps({'claims_without_details': len(rows), 'projects_found': len(details), 'claims_enriched': done,
                      'with_company_role': role, 'write': a.write}))


if __name__ == '__main__':
    main()
