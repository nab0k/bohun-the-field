#!/usr/bin/env python3
"""Aggregate proof of stored Inoreader body hashes; no plaintext logging."""
import hashlib
import json
import os


def main():
    import psycopg
    password=os.environ.get('SUPABASE_DB_PASSWORD')
    if not password:raise SystemExit('DB_PASSWORD_MISSING')
    params={'host':os.environ.get('SUPABASE_DB_HOST','aws-1-eu-central-1.pooler.supabase.com'),
            'port':int(os.environ.get('SUPABASE_DB_PORT','5432')),'dbname':'postgres',
            'user':os.environ.get('SUPABASE_DB_USER','postgres.tecrucawzukxqtchpxrh'),
            'password':password,'sslmode':'require','connect_timeout':20}
    with psycopg.connect(**params) as conn:
        with conn.cursor() as cur:
            cur.execute('SET TRANSACTION READ ONLY')
            cur.execute("SET LOCAL statement_timeout='15s'")
            cur.execute("""SELECT i.raw_data#>>'{content_capture,text}',
                   i.raw_data#>>'{content_capture,text_sha256}'
                 FROM intelligence.items i JOIN intelligence.sources s ON s.id=i.source_id
                 WHERE s.name='Inoreader BOHUN'
                   AND i.raw_data#>>'{content_capture,status}'='BODY_PRESENT_COMPLETENESS_UNVERIFIED'
                 ORDER BY i.updated_at DESC,i.id DESC LIMIT 12""")
            rows=cur.fetchall()
        conn.rollback()
    verified=sum(isinstance(t,str) and 0<len(t.encode())<=128*1024 and
                 hashlib.sha256(t.encode()).hexdigest()==h for t,h in rows)
    print(json.dumps({'proof':'stored_provider_body_readback','checked':len(rows),
                      'valid_text_hashes':verified,'completeness':'UNKNOWN','public_release':False}))
    if not rows or verified!=len(rows):raise SystemExit('STORED_BODY_PROOF_FAILED')


if __name__=='__main__':main()
