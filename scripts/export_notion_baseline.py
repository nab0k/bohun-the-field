#!/usr/bin/env python3
"""Private read-only Notion Companies baseline for crossmatching. NOT a public exporter.
Requires NOTION_TOKEN, optional NOTION_COMPANIES_DATA_SOURCE.
Never prints names or secrets; only count. Output is private JSONL.
"""
import json,os,sys,time,urllib.request,urllib.error
from pathlib import Path
def main():
 token=os.getenv("NOTION_TOKEN")
 if not token:raise SystemExit("NOTION_TOKEN absent: baseline export not performed")
 data_source=os.getenv("NOTION_COMPANIES_DATA_SOURCE","ec8c6312-3bfc-48e8-9a27-3c5827e72fda")
 output=Path(sys.argv[1] if len(sys.argv)>1 else ".local/notion-baseline.jsonl")
 output.parent.mkdir(parents=True,exist_ok=True)
 cursor=None;seen=set();records=[]
 while True:
  body={"page_size":100}
  if cursor:body["start_cursor"]=cursor
  url="https://api.notion.com/v1/data_sources/"+data_source+"/query"
  req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+token,"Notion-Version":"2025-09-03","Content-Type":"application/json"},method="POST")
  for attempt in range(6):
   try:
    with urllib.request.urlopen(req,timeout=35) as res:payload=json.load(res)
    break
   except urllib.error.HTTPError as e:
    if e.code not in (429,500,502,503) or attempt==5:raise
    time.sleep(min(2**attempt,16))
  for page in payload.get("results",[]):
   if page.get("object")!="page" or page.get("archived") or page.get("in_trash"):continue
   props=page.get("properties") or {}
   title=props.get("Name") or {}
   name="".join(v.get("plain_text","") for v in title.get("title",[])).strip()
   if not name:continue
   ident=page.get("id")
   if ident in seen:raise RuntimeError("Duplicated Notion ID")
   seen.add(ident)
   records.append({"notion_page_id":ident,"name":name,"country":((props.get("Country") or {}).get("select") or {}).get("name"),"website":(props.get("Website") or {}).get("url"),"review_status":((props.get("Review status") or {}).get("select") or {}).get("name")})
  if not payload.get("has_more"):break
  nxt=payload.get("next_cursor")
  if not nxt or nxt==cursor:raise RuntimeError("Unreliable Notion pagination")
  cursor=nxt
 if not records:raise RuntimeError("No records: refusing empty baseline")
 output.write_text("".join(json.dumps(item,ensure_ascii=False)+"\n" for item in records),encoding="utf-8")
 print(json.dumps({"notion_records":len(records),"unique_ids":len(seen),"output":str(output)}))
if __name__=="__main__":main()
