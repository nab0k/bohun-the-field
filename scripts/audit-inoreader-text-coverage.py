#!/usr/bin/env python3
"""Read-only audit of existing Inoreader rows in Supabase.
Outputs field/key coverage, never article text, titles, URLs or credentials.
No Inoreader API use, no cloud writes, no Gemini calls.
"""
import json
import os
from collections import Counter
from html import unescape
from html.parser import HTMLParser

class Strip(HTMLParser):
 def __init__(self): super().__init__(); self.out=[]
 def handle_data(self, data): self.out.append(data)
def visible(value):
 if not isinstance(value,str):return ""
 s=Strip()
 try:s.feed(value);return unescape(" ".join(s.out)).strip()
 except Exception:return value
def main():
 import psycopg
 password=os.environ.get("SUPABASE_DB_PASSWORD")
 if not password:raise RuntimeError("NO_EXISTING_SUPABASE_READ_CREDENTIAL")
 host=os.environ.get("SUPABASE_DB_HOST","aws-1-eu-central-1.pooler.supabase.com")
 user=os.environ.get("SUPABASE_DB_USER","postgres.tecrucawzukxqtchpxrh")
 stats=Counter(); lengths=[]; keys=Counter()
 with psycopg.connect(host=host,port=int(os.environ.get("SUPABASE_DB_PORT","5432")),dbname="postgres",user=user,password=password,sslmode="require",connect_timeout=20,options="-c default_transaction_read_only=on") as conn:
  with conn.cursor() as cur:
   cur.execute("""SELECT i.raw_data FROM intelligence.items i
 JOIN intelligence.sources s ON s.id=i.source_id
 WHERE s.name=%s ORDER BY i.published_at DESC NULLS LAST,i.id DESC LIMIT 20""",("Inoreader BOHUN",))
   for (raw,) in cur:
    if isinstance(raw,str):
     try:raw=json.loads(raw)
     except ValueError:raw={}
    raw=raw if isinstance(raw,dict) else {}
    keys.update(raw.keys())
    # Inoreader/Google Reader JSON may use object content envelopes.
    candidates=[]
    for field in ("content","summary","description","body","full_content","fulltext","text"):
     v=raw.get(field)
     if isinstance(v,dict):v=v.get("content") or v.get("value") or ""
     if isinstance(v,str) and v.strip():
      candidates.append((field,len(visible(v))))
    best=max((n for _,n in candidates),default=0)
    stats["rows"]+=1
    stats["rows_with_any_text"]+=bool(best)
    stats["rows_over_500_visible_chars"]+=best>=500
    stats["rows_over_2000_visible_chars"]+=best>=2000
    stats["rows_over_5000_visible_chars"]+=best>=5000
    lengths.append(best)
 print(json.dumps({"scope":"20 newest Inoreader BOHUN Supabase metadata rows","counts":stats,"raw_data_keys_frequency":keys,"visible_character_lengths":{"min":min(lengths,default=0),"max":max(lengths,default=0),"mean":round(sum(lengths)/len(lengths),1) if lengths else 0,"total":sum(lengths)},"caution":"Character length is only a proxy, not proof that text is full original article. No text was printed or exported."},ensure_ascii=False,default=dict))
if __name__=="__main__":main()
