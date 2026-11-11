#!/usr/bin/env python3
"""SOURCES-02 P0: direct publisher RSS -> item-level evidence; no Inoreader, no cloud writes."""
import argparse, datetime, hashlib, json, urllib.request, urllib.error, xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit
FEEDS = [
 {"source_id":"SRC-NAVALNEWS","publisher":"Naval News","feed_url":"https://www.navalnews.com/feed/","source_family":"independent_trade_media","rights":"Metadata + short excerpt for internal review only; republication unapproved"},
 {"source_id":"SRC-UKDEFJOURNAL","publisher":"UK Defence Journal","feed_url":"https://ukdefencejournal.org.uk/feed/","source_family":"independent_trade_media","rights":"Metadata + short excerpt for internal review only; republication unapproved"},
]
def val(e, name):
 x=e.find(name)
 return ''.join(x.itertext()).strip() if x is not None else None
def collect(source):
 req=urllib.request.Request(source["feed_url"],headers={"User-Agent":"BOHUN-SOURCES-02/1.0","Accept":"application/rss+xml,application/atom+xml,application/xml"})
 with urllib.request.urlopen(req,timeout=20) as response:
  raw=response.read(2_000_001)
  if len(raw)>2_000_000: raise ValueError("feed too large")
  fetched=datetime.datetime.now(datetime.timezone.utc).isoformat()
 root=ET.fromstring(raw)
 entries=root.findall("./channel/item")
 if not entries: raise ValueError("no RSS items; check feed format")
 records=[]
 for item in entries[:5]:
  title=val(item,"title") or ""
  link=val(item,"link")
  excerpt=val(item,"description") or ""
  pub=val(item,"pubDate")
  if not link or urlsplit(link).scheme not in ("https","http"): continue
  # Hash exact fetched source fields, not an invented article body.
  evidence_text=json.dumps({"title":title,"url":link,"excerpt":excerpt,"published":pub},ensure_ascii=False,sort_keys=True)
  digest=hashlib.sha256(evidence_text.encode()).hexdigest()
  records.append({**source,"evidence_id":"EVD-"+digest[:20],"original_url":link,"published_at_raw":pub,
   "fetched_at":fetched,"content_sha256":digest,"hash_scope":"RSS item fields, NOT full article",
   "original_excerpt":excerpt[:500],"excerpt_locator":"rss/channel/item/description",
   "title":title,"raw_source_version_sha256":hashlib.sha256(raw).hexdigest(),
   "qa_status":"HOLD","publication_status":"NONPUBLISHABLE","source_url":source["feed_url"]})
 return records
def main():
 p=argparse.ArgumentParser();p.add_argument("--out",default="output/sources02-p0");a=p.parse_args()
 out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
 report={"generated_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"inoreader_requests":0,"cloud_writes":0,"sources":[]}
 for src in FEEDS:
  try:
   records=collect(src)
   (out/(src["source_id"]+".json")).write_text(json.dumps(records,ensure_ascii=False,indent=2)+"\n")
   report["sources"].append({"source_id":src["source_id"],"status":"PASS" if records else "FAIL","items":len(records)})
  except (OSError,ValueError,ET.ParseError) as e:
   report["sources"].append({"source_id":src["source_id"],"status":"BLOCKED","reason":type(e).__name__})
 report["regulatory"]={"source_id":"SRC-EURLEX","official_directory":"https://eur-lex.europa.eu/content/help/search/predefined-rss.html?locale=en",
  "status":"BLOCKED","reason":"Direct regulatory item endpoint and item-level fetch not yet verified; do not fabricate"}
 (out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
 print(json.dumps(report,ensure_ascii=False,indent=2))
 return 0 if all(x["status"]=="PASS" for x in report["sources"]) and report["regulatory"]["status"]=="PASS" else 2
if __name__=="__main__": raise SystemExit(main())
