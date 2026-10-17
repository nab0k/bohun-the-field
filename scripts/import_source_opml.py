#!/usr/bin/env python3
"""Import RSS/Atom OPML collections to internal source candidate inventory.
No feed is called verified until probed by a separate rights-aware check.
Usage: python3 scripts/import_source_opml.py input.opml [other.opml...] --out .local/sources.jsonl
"""
import argparse,hashlib,json,urllib.parse,xml.etree.ElementTree as ET
from pathlib import Path
def parse_opml(payload,origin):
 root=ET.fromstring(payload)
 if root.tag.lower()!="opml":raise ValueError("Not OPML")
 for elem in root.iter("outline"):
  link=elem.attrib.get("xmlUrl") or elem.attrib.get("xmlurl")
  if not link:continue
  u=urllib.parse.urlsplit(link)
  if u.scheme not in ("http","https") or not u.netloc or u.username or u.password:continue
  name=elem.attrib.get("title") or elem.attrib.get("text") or u.netloc
  yield {"source_id":"rss:"+hashlib.sha256(link.encode()).hexdigest()[:20],"name":name,"rss_url":link,"homepage":elem.attrib.get("htmlUrl"),"source_collection":origin,"category":"UNCLASSIFIED","quality_status":"CANDIDATE_UNVERIFIED","rights_status":"UNREVIEWED","public_release":False}
def import_files(paths):
 results={};errors=[]
 for path in paths:
  try:
   data=Path(path).read_bytes()
   for item in parse_opml(data,str(path)):results.setdefault(item["rss_url"],item)
  except Exception as e:errors.append({"path":str(path),"error":str(e)[:200]})
 return list(results.values()),errors
def main():
 p=argparse.ArgumentParser();p.add_argument("files",nargs="+");p.add_argument("--out",default=".local/source-candidates.jsonl");a=p.parse_args()
 records,errors=import_files(a.files)
 dest=Path(a.out);dest.parent.mkdir(parents=True,exist_ok=True)
 dest.write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in sorted(records,key=lambda x:x["rss_url"])),encoding="utf-8")
 print(json.dumps({"opml_inputs":len(a.files),"unique_unverified_feeds":len(records),"errors":errors,"output":str(dest),"verified_feeds":0},indent=2))
if __name__=="__main__":main()
