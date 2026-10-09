#!/usr/bin/env python3
"""Import TED Collector v4 SQLite data into the existing intelligence schema."""
import argparse
import datetime as dt
import getpass
import json
import os
import sqlite3
import sys
from pathlib import Path


def timestamp(value):
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    try:
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        parsed = dt.datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    except ValueError:
        pass
    try:
        return dt.datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def json_object(raw):
    if not raw:
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {'payload': value}
    except (TypeError, ValueError):
        return {'unparsed_raw_json': raw}


def read_sqlite(path):
    uri = 'file:' + str(Path(path).expanduser().resolve()) + '?mode=ro'
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {'tenders', 'tender_changes'} <= tables:
            raise ValueError('SQLite must contain tenders and tender_changes')
        tenders = [dict(r) for r in db.execute('SELECT * FROM tenders ORDER BY notice_id')]
        changes = [dict(r) for r in db.execute('SELECT * FROM tender_changes ORDER BY id')]
        return tenders, changes
    finally:
        db.close()


def validate(tenders, changes):
    ids = [str(t.get('notice_id') or '').strip() for t in tenders]
    if not ids or any(not x for x in ids):
        raise ValueError('No tenders, or tender without notice_id')
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate notice_id in SQLite')
    missing = {str(c.get('notice_id')) for c in changes} - set(ids)
    if missing:
        raise ValueError(f'Change rows reference missing notices: {sorted(missing)[:5]}')
    if any(c.get('id') is None for c in changes):
        raise ValueError('Change without id')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', required=True, help='Path to local tenders.sqlite3')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--dry-run', action='store_true', help='Local validation only; no network')
    mode.add_argument('--apply', action='store_true', help='Write into Supabase in one transaction')
    args = parser.parse_args()
    tenders, changes = read_sqlite(args.db)
    validate(tenders, changes)
    print(f'VALIDATED tenders={len(tenders)} changes={len(changes)}')
    if args.dry_run:
        print('DRY RUN: no network connections, no writes')
        return

    try:
        import psycopg
        from psycopg.types.json import Jsonb
    except ImportError:
        sys.exit('Install dependency: python -m pip install "psycopg[binary]"')

    password = os.environ.get('SUPABASE_DB_PASSWORD') or getpass.getpass('Supabase database password (hidden): ')
    if not password:
        sys.exit('Missing password')
    # Avoid URI interpolation: passwords may contain URL-special characters.
    conn_args = dict(host=os.environ.get('SUPABASE_DB_HOST', 'aws-1-eu-central-1.pooler.supabase.com'),
                     port=int(os.environ.get('SUPABASE_DB_PORT', '5432')),
                     dbname='postgres',
                     user=os.environ.get('SUPABASE_DB_USER', 'postgres.tecrucawzukxqtchpxrh'),
                     password=password, sslmode='require', connect_timeout=20)
    try:
        with psycopg.connect(**conn_args) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute('SELECT to_regclass(%s), to_regclass(%s), to_regclass(%s), to_regclass(%s)',
                                ('intelligence.sources', 'intelligence.items', 'intelligence.opportunities', 'intelligence.item_changes'))
                    if any(x is None for x in cur.fetchone()):
                        raise RuntimeError('Missing required intelligence tables; run migrations first')
                    cur.execute('SELECT id FROM intelligence.sources WHERE name = %s ORDER BY id LIMIT 1', ('TED',))
                    row = cur.fetchone()
                    if row:
                        source_id = row[0]
                    else:
                        cur.execute('INSERT INTO intelligence.sources (name,url,source_type,collection_method) '
                                    'VALUES (%s,%s,%s,%s) RETURNING id',
                                    ('TED', 'https://ted.europa.eu/', 'procurement', 'official_api'))
                        source_id = cur.fetchone()[0]
                    id_map = {}
                    for t in tenders:
                        notice_id = str(t['notice_id']).strip()
                        raw = json_object(t.get('raw_json'))
                        # Keep ALL original collector fields, even if a dedicated field does not exist.
                        raw['_bohun_ted_v4'] = dict(t)
                        pub = timestamp(t.get('published'))
                        first = timestamp(t.get('first_seen'))
                        last = timestamp(t.get('last_seen'))
                        title = t.get('title') or f'TED notice {notice_id}'
                        cur.execute('''
                            INSERT INTO intelligence.items
                                (source_id, external_id, title, url, item_type, published_at,
                                 raw_data, first_seen_at, last_seen_at)
                            VALUES (%s,%s,%s,%s,'tender',%s,%s,%s,%s)
                            ON CONFLICT (source_id, external_id) DO UPDATE SET
                                title=EXCLUDED.title,
                                url=EXCLUDED.url,
                                published_at=COALESCE(EXCLUDED.published_at, intelligence.items.published_at),
                                raw_data=EXCLUDED.raw_data,
                                first_seen_at=COALESCE(intelligence.items.first_seen_at, EXCLUDED.first_seen_at),
                                last_seen_at=COALESCE(EXCLUDED.last_seen_at, intelligence.items.last_seen_at),
                                updated_at=NOW()
                            RETURNING id
                        ''', (source_id, notice_id, title, t.get('url'), pub, Jsonb(raw), first, last))
                        item_id = cur.fetchone()[0]
                        id_map[notice_id] = item_id
                        # Status is a collector estimate, not proof of an open tender.
                        cur.execute('''
                            INSERT INTO intelligence.opportunities
                                (item_id, opportunity_type, deadline_raw, value_raw, cpv, topic, status)
                            VALUES (%s,'tender',%s,%s,%s,%s,%s)
                            ON CONFLICT (item_id) DO UPDATE SET
                                deadline_raw=EXCLUDED.deadline_raw,
                                value_raw=EXCLUDED.value_raw,
                                cpv=EXCLUDED.cpv,
                                topic=EXCLUDED.topic,
                                status=EXCLUDED.status
                        ''', (item_id, t.get('deadline_raw'), t.get('value_raw'),
                              t.get('cpv'), t.get('topic'), t.get('status') or 'unknown'))
                    for c in changes:
                        cur.execute('''
                            INSERT INTO intelligence.item_changes
                                (item_id, source_change_id, detected_at, old_data, new_data)
                            VALUES (%s,%s,%s,%s,%s)
                            ON CONFLICT (item_id, source_change_id) DO UPDATE SET
                                detected_at=EXCLUDED.detected_at,
                                old_data=EXCLUDED.old_data,
                                new_data=EXCLUDED.new_data
                        ''', (id_map[str(c['notice_id'])], c['id'], timestamp(c.get('detected_at')),
                              Jsonb(json_object(c.get('old_json'))), Jsonb(json_object(c.get('new_json')))))
                    cur.execute('SELECT COUNT(*) FROM intelligence.items WHERE source_id=%s', (source_id,))
                    total_items = cur.fetchone()[0]
                    cur.execute('''SELECT COUNT(*) FROM intelligence.item_changes c
                                   JOIN intelligence.items i ON i.id=c.item_id WHERE i.source_id=%s''', (source_id,))
                    total_changes = cur.fetchone()[0]
        print(f'COMMITTED source_id={source_id} imported_tenders={len(tenders)} imported_changes={len(changes)}')
        print(f'VERIFIED cloud_ted_items={total_items} cloud_ted_changes={total_changes}')
    except Exception as exc:
        # Do not print exception string: drivers may include sensitive connection details.
        sys.exit(f'IMPORT FAILED ({type(exc).__name__}). Transaction rolled back. Check schema and connectivity.')


if __name__ == '__main__':
    main()
