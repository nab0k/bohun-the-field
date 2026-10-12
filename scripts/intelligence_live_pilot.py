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
PROJECT_ID="program:cordis:101289409"
EVENT_ID="event:grant:cordis:101289409"
PUBLICATION_ID="publication:source-update:cordis:101289409:v1"
NS={'c':'http://cordis.europa.eu'}
FIELD_SPECS={
 "project.euc_contribution":dict(tag="ecMaxContribution",predicate="eu_contribution",datatype="decimal",unit="EUR"),
 "project.ec_signature_date":dict(tag="ecSignatureDate",predicate="grant_agreement_signature_date",datatype="date",unit=None),
}
def stable_id(prefix,*parts):
 return prefix+":"+hashlib.sha256("\0".join(map(str,parts)).encode()).hexdigest()[:24]
def exact_locator(raw_text,tag,value):
 excerpt='<'+tag+'>'+value+'</'+tag+'>'
 start=raw_text.find(excerpt)
 if start<0:raise ValueError("EXACT_EVIDENCE_NOT_FOUND:"+tag)
 return excerpt,dict(type="xml_element",xpath="/project/"+tag,char_start=start,char_end=start+len(excerpt))
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
  # Typed project fact from actual official XML, using EXISTING entity/program/fact tables.
  root=ET.fromstring(raw)
  pick=lambda field:root.findtext('c:'+field,default='',namespaces=NS)
  assert pick('id')=='101289409' and pick('acronym')=='SMART'
  project_id=PROJECT_ID
  now=NOW()
  c.execute("""INSERT OR IGNORE INTO entity
    (id,kind,slug,name,summary_en,country_iso2,lat,lon,primary_source_url,status,status_reason,created_at,updated_at)
    VALUES (?,?,?,?,?,NULL,NULL,NULL,?,'candidate',?,?,?)""",
    (project_id,'program','cordis-smart-101289409',pick('title'),
     'Source-reported EU research project. Not an awarded military procurement.',URL,
     'INTERNAL_QA_HOLD_NO_ENTITY_CROSSWALK',now,now))
  c.execute("""INSERT OR IGNORE INTO program (entity_id,budget_amount,budget_currency,period_start,period_end)
    VALUES (?,?,?,?,?)""",(project_id,None,None,pick('startDate'),pick('endDate')))
  raw_id=c.execute("SELECT id FROM raw_item WHERE source_id=? AND external_id=? AND content_hash=?",
       (SOURCE,EXTERNAL_ID,sha)).fetchone()[0]
  for field,spec in FIELD_SPECS.items():
   tag=spec['tag']
   val=pick(tag)
   assert val and ('<'+tag+'>'+val+'</'+tag+'>') in raw.decode('utf-8')
   evidence='<'+tag+'>'+val+'</'+tag+'>'
   if not c.execute("SELECT id FROM fact WHERE entity_id=? AND field=? AND source_url=? AND raw_item_id=?",(project_id,field,URL,raw_id)).fetchone():
    c.execute("""INSERT INTO fact(entity_id,field,value,source_url,raw_item_id,evidence,extracted_by,evidence_verified,extracted_at)
      VALUES (?,?,?,?,?,?,'rule',1,?)""",(project_id,field,val,URL,raw_id,evidence,now))
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
  facts=c.execute("SELECT field,value,evidence_verified FROM fact WHERE entity_id=? ORDER BY field",(PROJECT_ID,)).fetchall()
  assert len(facts)==2 and all(f[2]==1 for f in facts),facts
  result['typed_fact_count']=len(facts)
  (ROOT/"readback.json").write_text(json.dumps(result,indent=2))
  c.close()
 elif mode=="export":
  assert (ROOT/"readback.json").exists()
  read=json.loads((ROOT/"readback.json").read_text())
  # Content comes from actual persisted original XML, not a manually authored static fixture.
  c=db();row=c.execute("SELECT payload,content_hash FROM raw_item WHERE id=?",(read["row_id"],)).fetchone()
  root=ET.fromstring(row[0]);assert row[1]==read["sha256"]
  # The coordinator's organisation country is NOT a programme venue or contract location.
  coords=root.find(".//c:organization[@type='coordinator']/c:address/c:country",NS)
  coordinator_country=coords.text if coords is not None else None
  facts=c.execute("SELECT id,field,value,evidence,extracted_by,evidence_verified FROM fact WHERE entity_id=? ORDER BY field",(PROJECT_ID,)).fetchall()
  raw_text=row[0]
  source={"source_id":SOURCE,"publisher":"European Commission CORDIS","original_url":URL,
    "source_family":"official_project_api","rights":{"status":"UNKNOWN","public_reuse":False}}
  document_id=stable_id("document",SOURCE,EXTERNAL_ID,row[1])
  document={"document_id":document_id,"source_id":SOURCE,"external_id":EXTERNAL_ID,
    "raw_item_id":read["row_id"],"content_sha256":row[1],"media_type":"application/xml",
    "original_url":URL,"original_bytes_stored":True}
  evidence=[];claims=[]
  for fact_id,field,value,excerpt,extracted_by,verified in facts:
   spec=FIELD_SPECS[field]
   exact,locator=exact_locator(raw_text,spec['tag'],value)
   assert excerpt==exact and verified==1 and extracted_by=='rule'
   evidence_id=stable_id("evidence",document_id,field,exact)
   claim_id=stable_id("claim",PROJECT_ID,spec['predicate'],value,evidence_id)
   evidence.append({"evidence_id":evidence_id,"document_id":document_id,"exact_excerpt":exact,
      "locator":locator,"extraction_version":"rule:cordis-project-v1","fact_row_id":fact_id})
   claims.append({"claim_id":claim_id,"subject_id":PROJECT_ID,"predicate":spec['predicate'],
      "object":{"value":value,"datatype":spec['datatype'],"unit":spec['unit']},
      "evidence_ids":[evidence_id],"epistemic_status":"OBSERVED",
      "independent_validation":"NOT_ENOUGH_INFO"})
  event={"event_id":EVENT_ID,"event_type":"grant","stage":"signed","actor_entity_ids":[],
    "subject_entity_id":PROJECT_ID,"claim_ids":[x['claim_id'] for x in claims],
    "geo":{"country_iso2":coordinator_country,"role":"COORDINATOR_COUNTRY_ONLY","coordinates":None},
    "dedup_key":"CORDIS:101289409"}
  publication={"publication_id":PUBLICATION_ID,"type":"SOURCE_UPDATE","event_id":EVENT_ID,
    "claim_ids":[x['claim_id'] for x in claims],"version":1,"qa_status":"HOLD",
    "public_release":False,"published_at":None,"blocking_reasons":[
      "SOURCE_RIGHTS_NOT_REVIEWED","INDEPENDENT_CLAIM_QA_NOT_COMPLETED","ENTITY_CROSSWALK_NOT_COMPLETED"]}
  contract={"contract_version":"bohun-intelligence-vertical-slice-v1","internal_only":True,
    "sources":[source],"documents":[document],"evidence":evidence,"claims":claims,
    "events":[event],"publications":[publication]}
  # Internal candidate view: source update only; no model-inferred claims, company ID or fake pin.
  feed={"internal_only":True,"qa_status":"HOLD","contract":contract,"items":[{
     "event_id":EVENT_ID,"publication_id":PUBLICATION_ID,"source_id":SOURCE,"source_url":URL,"source_sha256":row[1],
     "headline":"CORDIS official project record · SMART · GA 101289409",
     "summary":"Original European Commission CORDIS XML captured, hashed and deduplicated. No independent company/contract analysis approved.",
     "country_iso2":coordinator_country,"geo_role":"COORDINATOR_COUNTRY_ONLY","coordinates":None,
     "claim_ids":[x['claim_id'] for x in claims],
     "typed_facts":[{"field":f[1],"value":f[2],"exact_evidence":f[3]} for f in facts],
     "company_id":None,"regulation_id":None,"analysis_id":None,
     "source_kind":"EU_PROJECT_API","qa":{"decision":"HOLD","public_release":False},
     "raw_item_id":read["row_id"]}]}
  folder=Path("public/atlas");folder.mkdir(parents=True,exist_ok=True)
  (folder/"pilot-internal-feed.json").write_text(json.dumps(feed,ensure_ascii=False,indent=2))
  (ROOT/"internal-feed.json").write_text(json.dumps(feed,ensure_ascii=False,indent=2))
  (ROOT/"contract-packet.json").write_text(json.dumps(contract,ensure_ascii=False,indent=2))
  print(json.dumps({"stage":"export","items":1,"sources":1,"documents":1,"evidence":len(evidence),
    "claims":len(claims),"events":1,"publications":1,"db_id":read["row_id"],"qa":"HOLD","public_release":False}))
  c.close()
 elif mode=="validate":
  packet=json.loads((ROOT/"contract-packet.json").read_text())
  c=db();row=c.execute("SELECT payload,content_hash FROM raw_item WHERE source_id=? AND external_id=?",(SOURCE,EXTERNAL_ID)).fetchone();c.close()
  assert row and hashlib.sha256(row[0].encode()).hexdigest()==row[1]
  ids={k:{x[k[:-1]+"_id"] for x in packet[k]} for k in ("sources","documents","events","publications")}
  ids["evidence"]={x["evidence_id"] for x in packet["evidence"]};ids["claims"]={x["claim_id"] for x in packet["claims"]}
  assert all(len(v)==len(packet[k]) for k,v in ids.items())
  assert all(x["source_id"] in ids["sources"] for x in packet["documents"])
  assert all(x["document_id"] in ids["documents"] for x in packet["evidence"])
  for x in packet["evidence"]:
   a,b=x["locator"]["char_start"],x["locator"]["char_end"]
   assert row[0][a:b]==x["exact_excerpt"]
  assert all(set(x["evidence_ids"])<=ids["evidence"] for x in packet["claims"])
  assert all(set(x["claim_ids"])<=ids["claims"] for x in packet["events"])
  assert all(x["event_id"] in ids["events"] and set(x["claim_ids"])<=ids["claims"] for x in packet["publications"])
  assert all(x["qa_status"]=="HOLD" and x["public_release"] is False and x["published_at"] is None for x in packet["publications"])
  result={"stage":"validate","pass":True,"counts":{k:len(v) for k,v in ids.items()},
    "source_hash_verified":True,"exact_evidence_verified":len(packet["evidence"]),"qa_fail_closed":True}
  (ROOT/"validation.json").write_text(json.dumps(result,indent=2));print(json.dumps(result))
 else:raise ValueError(mode)
if __name__=="__main__":main(sys.argv[1])
