#!/usr/bin/env python3
"""Read ONE existing Inoreader row from canonical Supabase; local evidence-store replay.
Never fetch Inoreader, never call Gemini, never write cloud data.
"""
import argparse,datetime,hashlib,json,os,sqlite3
from pathlib import Path
P=Path("output/inoreader-e2e"); DB=P/"evidence.sqlite3"
def canonical(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(",",":"),default=str).encode()
def read_existing(pinned_external_id=None):
 import psycopg
 password=os.environ.get("SUPABASE_DB_PASSWORD")
 if not password:raise RuntimeError("NO_EXISTING_SUPABASE_READ_CREDENTIAL")
 with psycopg.connect(host=os.environ.get("SUPABASE_DB_HOST","aws-1-eu-central-1.pooler.supabase.com"),port=int(os.environ.get("SUPABASE_DB_PORT","5432")),dbname="postgres",user=os.environ.get("SUPABASE_DB_USER","postgres.tecrucawzukxqtchpxrh"),password=password,sslmode="require",connect_timeout=20,options="-c default_transaction_read_only=on") as conn:
  with conn.cursor() as cur:
   cur.execute("""SELECT i.id,i.external_id,i.title,i.url,i.published_at,i.raw_data,s.name
 FROM intelligence.items i JOIN intelligence.sources s ON s.id=i.source_id
 WHERE s.name=%s AND i.url LIKE 'https:%%' AND i.external_id IS NOT NULL
 AND (%s::text IS NULL OR i.external_id=%s::text)
 ORDER BY i.published_at DESC NULLS LAST,i.id DESC LIMIT 1""",("Inoreader BOHUN",pinned_external_id,pinned_external_id))
   row=cur.fetchone()
 if not row:raise RuntimeError("NO_EXISTING_INOREADER_ROW")
 return dict(supabase_item_id=str(row[0]),external_id=row[1],title=row[2],url=row[3],published_at=row[4].isoformat() if row[4] else None,raw_data=row[5],source_name=row[6])
def open_db():
 P.mkdir(parents=True,exist_ok=True)
 c=sqlite3.connect(DB)
 c.executescript("""CREATE TABLE IF NOT EXISTS source(source_id TEXT PRIMARY KEY,origin TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS evidence(evidence_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,external_id TEXT NOT NULL,content_sha256 TEXT NOT NULL,source_url TEXT NOT NULL,obtained_at TEXT NOT NULL,payload TEXT NOT NULL,document_kind TEXT NOT NULL,original_available INTEGER NOT NULL,license_status TEXT NOT NULL,UNIQUE(source_id,external_id,content_sha256));""")
 return c
def capture():
 pinned=json.loads((P/'source-manifest.json').read_text())['external_id'] if (P/'source-manifest.json').exists() else None
 item=read_existing(pinned)
 source_id="inoreader:BOHUN"; raw=canonical(item)
 sha=hashlib.sha256(raw).hexdigest()
 # Hash is of the Inoreader METADATA snapshot, never an original article hash.
 evidence_id="inoreader:item:"+hashlib.sha256((source_id+"\0"+item["external_id"]).encode()).hexdigest()
 c=open_db()
 before=c.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
 c.execute("INSERT OR IGNORE INTO source VALUES (?,?)",(source_id,"existing Supabase intelligence.items"))
 c.execute("""INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?)""",(evidence_id,source_id,item["external_id"],sha,item["url"],datetime.datetime.now(datetime.timezone.utc).isoformat(),raw.decode(),"inoreader_item_metadata",0,"UNREVIEWED"))
 c.commit()
 after=c.execute("SELECT COUNT(*) FROM evidence").fetchone()[0];c.close()
 (P/"source-manifest.json").write_text(json.dumps(dict(evidence_id=evidence_id,content_sha256=sha,hash_scope="Inoreader metadata only, not original article",original_available=False,license_status="UNREVIEWED",qa={"claim":"NOT_ENOUGH_INFO","decision":"HOLD","public_release":False},url=item["url"],external_id=item["external_id"],supabase_item_id=item["supabase_item_id"]),indent=2))
 print(json.dumps(dict(stage="capture",before=before,after=after,new=after-before,evidence_id=evidence_id,source_url=item["url"],supabase_item_id=item["supabase_item_id"],sha256=sha,qa="HOLD")))
def readback():
 c=open_db(); rows=c.execute("SELECT evidence_id,content_sha256,payload,original_available,license_status FROM evidence").fetchall();c.close()
 assert len(rows)==1,"EXPECTED_ONE_PERSISTED_ITEM"
 id,sha,payload,original,license=rows[0];assert hashlib.sha256(payload.encode()).hexdigest()==sha
 assert original==0 and license=="UNREVIEWED"
 result=dict(stage="fresh_process_readback",rows=1,evidence_id=id,sha256=sha,qa="HOLD",original_article="NOT_STORED",cloud_independent_run="NOT_TESTED")
 (P/"readback.json").write_text(json.dumps(result,indent=2));print(json.dumps(result))
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("operation",choices=("capture","readback"));a=p.parse_args()
 if a.operation=="capture":capture()
 else:readback()
