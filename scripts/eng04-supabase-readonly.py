#!/usr/bin/env python3
"""ENG-04: read-only lookup of a publicly reported Frequentis event in existing Supabase.
No writes, no DDL, no Gemini, no tokens printed.
"""
import datetime,json,os,sys
from pathlib import Path
import psycopg
path=Path("output/eng04/supabase-frequentis-readonly.json")
path.parent.mkdir(parents=True,exist_ok=True)
out={"status":"NOT_VERIFIED","read_only":True,"target":"Frequentis AG","query_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"matches":[]}
try:
    with psycopg.connect(host=os.getenv("SUPABASE_DB_HOST","aws-1-eu-central-1.pooler.supabase.com"),port=int(os.getenv("SUPABASE_DB_PORT","5432")),user=os.getenv("SUPABASE_DB_USER","postgres.tecrucawzukxqtchpxrh"),password=os.environ["SUPABASE_DB_PASSWORD"],dbname="postgres",sslmode="require",connect_timeout=20) as db:
        with db.cursor() as cur:
            cur.execute("BEGIN READ ONLY")
            cur.execute("""SELECT i.id,i.title,i.url,i.published_at,s.name
                FROM intelligence.items i JOIN intelligence.sources s ON s.id=i.source_id
                WHERE i.title ILIKE %s OR i.url ILIKE %s
                ORDER BY i.published_at DESC NULLS LAST LIMIT 30""",("%frequentis%","%frequentis%"))
            for rid,title,url,pub,source in cur.fetchall():
                out["matches"].append({"id":str(rid),"title":str(title)[:230],"url":str(url)[:450],
                    "published_at":str(pub) if pub is not None else None,"source_name":source})
            db.rollback()
    out["status"]="PASS";out["match_count"]=len(out["matches"])
except Exception as err:
    out["error_type"]=type(err).__name__
    # Avoid copying provider error strings that may include secret connection information.
finally:
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"ENG-04 Supabase read-only: status={out['status']}, match_count={len(out['matches'])}, error_type={out.get('error_type','none')}")
if out["status"]!="PASS":sys.exit(1)
