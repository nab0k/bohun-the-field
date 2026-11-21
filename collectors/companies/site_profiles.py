"""Site company profiles <-> companies.profile_* rows (B5CC-01, design B v1 slice 0).

One reviewed site profile (schema bohun-company-profile-v1) is stored as:
  profile_claim  one row per profile section, field_path '/site_profile/<section>',
                 claim_value = the section JSON, qa_status 'APPROVED' once the owner accepted it;
  profile_source one row per https evidence link of the profile;
  profile_claim_source every section claim linked to every source of that company.
public_release stays false (enforced by the table CHECK); publication is decided by the builder
gate (APPROVED) and the site release, not by this table.
"""
from __future__ import annotations

import hashlib
import json

SECTION_PREFIX = '/site_profile/'
META = '_meta'

SOURCE_KIND = [
    ('GLEIF', 'legal_entity_identifier_reference'),
    ('register', 'government_register'),
    ('Brønnøysund', 'government_register'),
    ('USAspending', 'contract_register'),
    ('CORDIS', 'eu_programme_record'),
    ('European Commission', 'eu_programme_record'),
    ('AMF', 'issuer_market_announcement'),
    ('Annual Report', 'issuer_corporate_disclosure'),
    ('Company website', 'company_website'),
    ('Company release', 'issuer_press_release'),
    ('Company press release', 'issuer_press_release'),
    ('Company results release', 'issuer_press_release'),
    ('Company financial review', 'issuer_press_release'),
    ('GOV.UK', 'government_administrative_record'),
    ('Gov.pl', 'government_administrative_record'),
    ('Ministry', 'government_administrative_record'),
    ('Government', 'government_administrative_record'),
    ('ORF', 'media_report'),
    ('KIVI', 'media_report'),
]


def canon(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha(value) -> str:
    return hashlib.sha256(canon(value).encode('utf-8')).hexdigest()


def source_kind(label: str) -> str:
    for key, kind in SOURCE_KIND:
        if key.lower() in (label or '').lower():
            return kind
    return 'counterparty_or_third_party'


def assessment(profile: dict) -> str:
    statuses = [e.get('status') for e in profile.get('evidence', [])]
    if 'Verified' in statuses:
        return 'cross_source'
    return 'company_claim'


def to_rows(slug: str, profile: dict, company_id: str, next_source_id: int, next_claim_id: int,
            as_of: str, note: str):
    """Return (sources, claims, links) for one profile; ids are assigned from the given counters."""
    sources, seen = [], {}
    for i, e in enumerate(profile.get('evidence', [])):
        url = e.get('href') or ''
        if not url.startswith('https://') or url in seen:
            continue
        seen[url] = next_source_id
        sources.append({'id': next_source_id, 'company_id': company_id, 'source_ref': f'b4ce:{slug}:s{i + 1}',
                        'title': (e.get('source') or 'source')[:200], 'source_url': url,
                        'source_kind': source_kind(e.get('source', '')), 'as_of': as_of})
        next_source_id += 1
    claims = []
    sections = dict(profile)
    sections[META] = {'slug': slug}
    level = assessment(profile)
    for key in sorted(sections):
        value = sections[key]
        claims.append({'id': next_claim_id, 'company_id': company_id, 'claim_ref': f'site:{key}'[:100],
                       'field_path': SECTION_PREFIX + key, 'claim_value': value,
                       'assessment': level, 'review_note': note, 'content_sha256': sha(value),
                       'qa_status': 'APPROVED', 'public_release': False})
        next_claim_id += 1
    links = [{'company_id': company_id, 'claim_id': c['id'], 'source_id': s['id']} for c in claims for s in sources]
    return sources, claims, links


def from_rows(claim_rows):
    """Rebuild {slug: profile} from APPROVED profile_claim rows (dicts with company_id, field_path, claim_value)."""
    by_company = {}
    for r in claim_rows:
        if r.get('qa_status', 'APPROVED') != 'APPROVED' or not r['field_path'].startswith(SECTION_PREFIX):
            continue
        key = r['field_path'][len(SECTION_PREFIX):]
        value = r['claim_value']
        if isinstance(value, str) and value[:1] in '{["':
            value = json.loads(value)
        by_company.setdefault(r['company_id'], {})[key] = value
    out = {}
    for sections in by_company.values():
        meta = sections.pop(META, None)
        if meta and meta.get('slug'):
            out[meta['slug']] = sections
    return out


def sql_literal(value) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return "'" + canon(value).replace("'", "''") + "'::jsonb"
    return "'" + str(value).replace("'", "''") + "'"


def insert_sql(sources, claims, links) -> str:
    """One transaction; every row is new (ids from max(id)+1), so a failure leaves nothing behind."""
    parts = ['begin;']
    for s in sources:
        cols = ['id', 'company_id', 'source_ref', 'title', 'source_url', 'source_kind', 'as_of']
        parts.append('insert into companies.profile_source (%s) values (%s);' % (
            ','.join(cols), ','.join(sql_literal(s[c]) for c in cols)))
    for c in claims:
        cols = ['id', 'company_id', 'claim_ref', 'field_path', 'claim_value', 'assessment', 'review_note',
                'content_sha256', 'qa_status', 'public_release']
        value = dict(c)
        lit =[sql_literal(value[col]) if col != 'claim_value' else
               "'" + canon(value[col]).replace("'", "''") + "'::jsonb" for col in cols]
        parts.append('insert into companies.profile_claim (%s) values (%s);' % (','.join(cols), ','.join(lit)))
    for l in links:
        parts.append('insert into companies.profile_claim_source (company_id, claim_id, source_id) values (%s,%s,%s);' % (
            sql_literal(l['company_id']), l['claim_id'], l['source_id']))
    parts.append('commit;')
    return '\n'.join(parts)


EXPORT_SQL = """select company_id, field_path, claim_value, qa_status
from companies.profile_claim where field_path like '/site_profile/%' and qa_status = 'APPROVED'
order by company_id, field_path"""

NOTICE = ('Reviewed reference company cards. Every claim is taken from a linked public source; quotes were '
          'checked against the source text. Company statements are marked Supported, not Verified.')


def build_document(claim_rows, keep=None):
    """Site file from APPROVED rows. `keep` = profiles not yet in the database, kept as they are."""
    profiles = dict(keep or {})
    profiles.update(from_rows(claim_rows))
    return {'schema': 'bohun-company-profile-v1', 'notice': NOTICE, 'profiles': profiles}


def main(argv=None):
    """python -m collectors.companies.site_profiles OUT.json [--keep CURRENT.json]
    Reads APPROVED site profiles from Supabase (SUPABASE_DB_PASSWORD) and writes the site file.
    Profiles present only in --keep (not in the database) are carried over unchanged and listed."""
    import argparse
    import psycopg
    from psycopg.rows import dict_row
    from collectors.companies.pg_store import connect_params
    ap = argparse.ArgumentParser()
    ap.add_argument('out')
    ap.add_argument('--keep')
    a = ap.parse_args(argv)
    with psycopg.connect(**connect_params(), row_factory=dict_row) as conn:
        rows = conn.execute(EXPORT_SQL).fetchall()
    keep = {}
    if a.keep:
        current = json.load(open(a.keep, encoding='utf-8'))['profiles']
        in_db = from_rows(rows)
        keep = {k: v for k, v in current.items() if k not in in_db}
    doc = build_document(rows, keep)
    with open(a.out, 'w', encoding='utf-8') as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print(f"profiles from database: {len(doc['profiles']) - len(keep)}; carried over: {sorted(keep)}")


if __name__ == '__main__':
    main()
