#!/usr/bin/env python3
"""Live Pilot 01: genuine first-party API XML -> existing D1 SQLite schema.
Ephemeral CI staging only, NEVER Supabase production and NEVER public release.
Use 'ingest' twice, 'readback' from a distinct process and 'export' for internal UI.
"""
import datetime,hashlib,json,os,re,sqlite3,sys,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
ROOT=Path("output/live-pilot");DB=ROOT/"pilot.sqlite3"
URL="https://cordis.europa.eu/project/id/101289409?format=xml"
SOURCE="cordis:project-api";EXTERNAL_ID="CORDIS:101289409";NOW=lambda:datetime.datetime.now(datetime.timezone.utc).isoformat()
def db():
 c=sqlite3.connect(DB);c.execute("PRAGMA foreign_keys=ON");return c
def initialize(c):
 if not c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='raw_item'").fetchone():
  c.executescript(Path("db/schema.sql").read_text())
 c.execute("INSERT OR IGNORE INTO source (id,kind,name,url,license,commercial_use) VALUES (?,?,?,?,?,?)",
           (SOURCE,"api","European Commission CORDIS Project API",URL,"Unknown, QA review","unknown"))
def capture():
 with urllib.request.urlopen(urllib.request.Request(URL,headers={"User-Agent":"BohunDefence Research QA/1.0","Accept":"application/xml,text/xml"}),timeout=25) as r:
  raw=r.read(1000000);ctype=r.headers.get("Content-Type","");status=r.status
 if status!=200 or "xml" not in ctype.lower():raise ValueError("OFFICIAL_XML_NOT_RETURNED")
 root=ET.fromstring(raw)
 if b"101289409" not in raw:raise ValueError("WRONG_CORDIS_PROJECT")
 return raw,ctype
def main(mode):
 ROOT.mkdir(parents=True,exist_ok=True)
 if mode=="ingest":
  raw,ctype=capture();sha=hashlib.sha256(raw).hexdigest()
  c=db();initialize(c)
  before=c.execute("SELECT COUNT(*) FROM raw_item WHERE source_id=? AND external_id=?",(SOURCE,EXTERNAL_ID)).fetchone()[0]
  c.execute("INSERT OR IGNORE INTO raw_item (source_id,external_id,fetched_at,content_hash,payload) VALUES (?,?,?,?,?)",
   (SOURCE,EXTERNAL_ID,NOW(),sha,raw.decode("utf-8")))
  after=c.execute("SELECT COUNT(*) FROM raw_item WHERE source_id=? AND external_id=?",(SOURCE,EXTERNAL_ID)).fetchone()[0]
  c.commit();c.close()
  log={"stage":"ingest","source_id":SOURCE,"external_id":EXTERNAL_ID,"url":URL,"sha256":sha,
       "xml_bytes":len(raw),"content_type":ctype,"before":before,"after":after,"new_versions":after-before,
       "qa":"HOLD","published":False,"staging_storage":"local existing D1/SQLite schema, not cloud"}
  with (ROOT/"ingest-log.jsonl").open("a") as f:f.write(json.dumps(log)+"\n")
  print(json.dumps(log))
 elif mode=="readback":
  assert DB.is_file(),"NO_PERSISTENT_DB_FILE"
  c=db();rows=c.execute("SELECT id,source_id,external_id,content_hash,payload FROM raw_item WHERE source_id=? AND external_id=?",(SOURCE,EXTERNAL_ID)).fetchall()
  assert len(rows)==1,{"expected_one_deduped_version":len(rows)}
  row=rows[0];raw=row[4].encode("utf-8");ET.fromstring(raw)
  assert hashlib.sha256(raw).hexdigest()==row[3]
  result={"stage":"RESTARTED_PROCESS_READBACK","pass":True,"rows":len(rows),"row_id":row[0],
    "source_id":row[1],"external_id":row[2],"sha256":row[3],"qa":"HOLD","cloud_persistence":"NOT_TESTED"}
  (ROOT/"readback.json").write_text(json.dumps(result,indent=2))
  print(json.dumps(result))
  c.close()
 elif mode=="export":
  assert (ROOT/"readback.json").exists()
  read=json.loads((ROOT/"readback.json").read_text())
  # Content comes from actual persisted original XML, not a manually authored static fixture.
  c=db();row=c.execute("SELECT payload,content_hash FROM raw_item WHERE id=?",(read["row_id"],)).fetchone()
  ET.fromstring(row[0]);assert row[1]==read["sha256"]
  # Internal candidate view: source update only; no model-inferred claims, company ID or fake pin.
  feed={"internal_only":True,"qa_status":"HOLD","items":[{
     "event_id":"CORDIS:101289409","source_id":SOURCE,"source_url":URL,"source_sha256":row[1],
     "headline":"CORDIS official project record · SMART · GA 101289409",
     "summary":"Original European Commission CORDIS XML captured, hashed and deduplicated. No independent company/contract analysis approved.",
     "country_iso2":None,"geo_role":"NONE","coordinates":None,
     "company_id":None,"regulation_id":None,"analysis_id":None,"publication_id":None,
     "source_kind":"EU_PROJECT_API","qa":{"decision":"HOLD","public_release":False},
     "raw_item_id":read["row_id"]}]}
  folder=Path("public/atlas");folder.mkdir(parents=True,exist_ok=True)
  (folder/"pilot-internal-feed.json").write_text(json.dumps(feed,ensure_ascii=False,indent=2))
  (ROOT/"internal-feed.json").write_text(json.dumps(feed,ensure_ascii=False,indent=2))
  print(json.dumps({"stage":"export","items":1,"db_id":read["row_id"],"qa":"HOLD","public_release":False}))
  c.close()
 else:raise ValueError(mode)
if __name__=="__main__":main(sys.argv[1])
