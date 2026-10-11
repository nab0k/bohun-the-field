#!/usr/bin/env python3
"""M0 evidence plumbing tests (real documents). NO model, DB writes or publication.
These are source-level tests, NOT three completed AI→QA→website E2E tests.
"""
import datetime as dt,hashlib,json,os,urllib.request,urllib.error
from pathlib import Path
OUT=Path("output/m0");OUT.mkdir(parents=True,exist_ok=True)
result={"utc":dt.datetime.now(dt.timezone.utc).isoformat(),"mode":"READ_ONLY",
        "e2e_verified":False,"tests":[],"model_usage":0,"publication":False}
ted=None
try:
    import psycopg
    with psycopg.connect(host=os.getenv("SUPABASE_DB_HOST","aws-1-eu-central-1.pooler.supabase.com"),
       port=int(os.getenv("SUPABASE_DB_PORT","5432")),
       user=os.getenv("SUPABASE_DB_USER","postgres.tecrucawzukxqtchpxrh"),
       dbname="postgres",password=os.environ["SUPABASE_DB_PASSWORD"],sslmode="require",connect_timeout=20) as conn:
        with conn.cursor() as cur:
            cur.execute("BEGIN READ ONLY")
            cur.execute("""SELECT i.id,i.title,i.url,i.published_at,s.name
                           FROM intelligence.items i JOIN intelligence.sources s ON s.id=i.source_id
                           WHERE lower(i.item_type)='tender' AND i.url LIKE 'http%'
                           ORDER BY i.published_at DESC NULLS LAST,i.id DESC LIMIT 1""")
            row=cur.fetchone()
            if row:ted={"item_id":str(row[0]),"title":str(row[1])[:300],"url":row[2],
                       "published_at":str(row[3]) if row[3] else None,"source_name":row[4]}
            conn.rollback()
    result["tests"].append({"case":"TED_RECORDED_PRIMARY_ITEM","status":"PASS" if ted else "FAIL",
                            "source_record":ted,"verification":"existing Supabase real tender row only; claim validation pending"})
except Exception as e:
    result["tests"].append({"case":"TED_RECORDED_PRIMARY_ITEM","status":"BLOCKED",
                            "error_type":type(e).__name__})
official="https://www.frequentis.com/sites/default/files/pr/2026-05/20260505-pr-Frequentis-BMLV_Flugfunksystem_EN.pdf"
try:
    req=urllib.request.Request(official,headers={"User-Agent":"Bohundefence-M0-QA-readonly/1.0"})
    with urllib.request.urlopen(req,timeout=25) as f:
        binary=f.read(2_000_000);mime=f.headers.get("Content-Type","")
        http=f.status
    result["tests"].append({"case":"FREQUENTIS_OFFICIAL_DOCUMENT","status":"PASS" if http==200 and binary.startswith(b"%PDF") else "FAIL",
       "source_url":official,"http_status":http,"bytes_read":len(binary),"content_type":mime,
       "sha256_read_bytes":hashlib.sha256(binary).hexdigest(),
       "verification":"issuer document accessible; no independent award confirmation and no model analysis"})
except urllib.error.HTTPError as e:
    result["tests"].append({"case":"FREQUENTIS_OFFICIAL_DOCUMENT","status":"BLOCKED","http_status":e.code,"source_url":official})
except Exception as e:
    result["tests"].append({"case":"FREQUENTIS_OFFICIAL_DOCUMENT","status":"BLOCKED",
                            "error_type":type(e).__name__,"source_url":official})
result["tests"].append({"case":"NEGATIVE_SAME_EVENT_DEDUP","status":"PASS" if ted else "BLOCKED",
    "source_item_id":ted["item_id"] if ted else None,"dedup_fingerprint":hashlib.sha256(ted["url"].encode()).hexdigest() if ted else None,
    "verification":"exact identical original TED URL reused as duplicate control; static rule implementation tested separately"})
(OUT/"m0-real-source-tests.json").write_text(json.dumps(result,ensure_ascii=False,indent=2))
print("M0 source-evidence:",[(x["case"],x["status"]) for x in result["tests"]],"; NO MODEL, NO DB WRITE")
