#!/usr/bin/env python3
"""Idempotent staging-only importer. Requires SUPABASE_STAGING_DATABASE_URL from GH Secrets.
No public schema, no publication, no Notion edits.
Usage: python3 scripts/import_harvest_to_supabase.py .local/harvest/cordis-horizon.jsonl
"""
import argparse,json,os,sys
def main():
 p=argparse.ArgumentParser();p.add_argument("files",nargs="+");p.add_argument("--batch-size",type=int,default=500);a=p.parse_args()
 dsn=os.getenv("SUPABASE_STAGING_DATABASE_URL")
 if not dsn:raise SystemExit("SUPABASE_STAGING_DATABASE_URL missing: no database write performed")
 if a.batch_size<1 or a.batch_size>5000:raise SystemExit("Unsafe batch-size")
 try:import psycopg
 except ImportError:raise SystemExit("Install psycopg[binary] in a controlled runner")
 rows=[];read=0;bad=0
 for path in a.files:
  with open(path,encoding="utf-8") as f:
   for line in f:
    if not line.strip():continue
    read+=1;obj=json.loads(line)
    if obj.get("publication_approved") is not False or obj.get("qa_status")!="PENDING" or obj.get("identity_status")!="UNVERIFIED":bad+=1;continue
    if not obj.get("candidate_id") or not obj.get("display_name") or not obj.get("source_id") or not obj.get("source_archive_sha256"):bad+=1;continue
    rows.append((obj["candidate_id"],obj["display_name"],obj.get("country_raw"),obj["source_id"],obj.get("source_url"),obj["source_archive_sha256"],obj.get("source_record_id")))
 if bad:raise SystemExit(f"Invalid/unsafe candidate records {bad}/{read}; refusing partial write")
 sql="""INSERT INTO bohun_staging.harvest_candidates(candidate_id,display_name,country_raw,source_id,source_url,source_archive_sha256,source_record_id,publication_approved)
 VALUES (%s,%s,%s,%s,%s,%s,%s,false)
 ON CONFLICT(candidate_id) DO UPDATE SET last_seen=now(),
 source_url=EXCLUDED.source_url, source_archive_sha256=EXCLUDED.source_archive_sha256,
 source_record_id=EXCLUDED.source_record_id
 RETURNING candidate_id"""
 sent=0
 with psycopg.connect(dsn,autocommit=False,connect_timeout=15) as conn:
  with conn.cursor() as cur:
   cur.execute("SELECT current_schema(),current_database()")
   for i in range(0,len(rows),a.batch_size):
    chunk=rows[i:i+a.batch_size]
    cur.executemany(sql,chunk)
    sent+=len(chunk)
  conn.commit()
 print(json.dumps({"input":read,"upserted":sent,"qa_approved":0,"public_release":0}))
if __name__=="__main__":main()
