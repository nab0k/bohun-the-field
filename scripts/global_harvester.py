#!/usr/bin/env python3
"""BOHUNDEFENCE mass company + intelligence source collector. Python stdlib only.
Usage: python3 scripts/global_harvester.py --out-dir out --max-download-mb 120
Network sources: official CORDIS project archives. Discovery feeds: explicitly named
seed sites; discover RSS via HTML link tags only, no invented feeds and no bypass.
All outputs local private artifacts; no Notion/Supabase or website writes.
"""
import argparse,csv,hashlib,io,json,os,re,time,urllib.request,urllib.parse,urllib.robotparser,zipfile
from pathlib import Path
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

ARCHIVES=[
 ("cordis-horizon","https://cordis.europa.eu/data/cordis-HORIZONprojects-csv.zip"),
 ("cordis-h2020","https://cordis.europa.eu/data/cordis-h2020projects-csv.zip"),
 ("cordis-fp7","https://cordis.europa.eu/data/cordis-fp7projects-csv.zip"),
]
SEEDS={
 "government":["https://www.defense.gov/News/","https://www.nato.int/","https://eda.europa.eu/","https://www.gov.uk/government/organisations/ministry-of-defence","https://www.canada.ca/en/department-national-defence.html","https://www.defence.gov.au/","https://www.defense.gouv.fr/","https://www.bundeswehr.de/","https://www.mod.go.jp/","https://www.dvidshub.net/"],
 "procurement":["https://ted.europa.eu/","https://www.usaspending.gov/","https://sam.gov/","https://www.contractsfinder.service.gov.uk/","https://www.find-tender.service.gov.uk/","https://www.canadabuys.canada.ca/","https://www.tenders.gov.au/"],
 "research":["https://cordis.europa.eu/","https://www.sbir.gov/","https://www.diana.nato.int/","https://www.rand.org/","https://www.csis.org/","https://www.iiss.org/","https://www.sipri.org/","https://rusi.org/"],
 "industry":["https://www.gifas.fr/","https://www.gicat.com/","https://www.gican.fr/","https://www.bdsv.eu/","https://www.bdli.de/","https://www.adsgroup.org.uk/","https://www.aiad.it/","https://www.soff.se/","https://www.aobp.cz/","https://www.ndia.org/","https://www.ausa.org/","https://www.afcea.org/","https://www.defenceindustries.ca/","https://www.sahaistanbul.org.tr/"],
 "press":["https://www.defensenews.com/","https://breakingdefense.com/","https://www.twz.com/","https://www.navalnews.com/","https://www.janes.com/","https://www.army-technology.com/","https://www.airforce-technology.com/","https://www.shephardmedia.com/"],
 "exhibitions":["https://www.eurosatory.com/","https://www.dsei.co.uk/","https://www.enforcetac.com/","https://www.idexuae.ae/","https://www.worlddefenseshow.com/","https://www.farnboroughairshow.com/","https://www.paris-air-show.com/"]
}
UA="BohunResearchHarvester/0.1 (public-source research; respect robots and rate limits)"
def sha(x):return hashlib.sha256(x).hexdigest()
def download(url,max_bytes,timeout=35):
 req=urllib.request.Request(url,headers={"User-Agent":UA,"Accept":"text/html,application/xml,application/zip,*/*"})
 with urllib.request.urlopen(req,timeout=timeout) as r:
  if int(r.headers.get("Content-Length",0))>max_bytes:raise ValueError("download exceeds size cap")
  data=r.read(max_bytes+1)
  if len(data)>max_bytes:raise ValueError("download exceeds size cap")
  return data
def safe_name(s):return re.sub(r"[^a-z0-9_.-]+","-",s.lower())[:100]
def norm(s):return re.sub(r"\s+"," ",re.sub(r"[^\w]+"," ",str(s or "").casefold())).strip()
def pick(row,*keys):
 lower={str(k).casefold():v for k,v in row.items()}
 for k in keys:
  v=lower.get(k.casefold())
  if v and str(v).strip():return str(v).strip()
 return ""
def load_existing(path):
 known=set()
 if not path:return known
 p=Path(path)
 for line in p.read_text(encoding="utf-8").splitlines():
  if not line.strip():continue
  r=json.loads(line)
  for key in ("name","display_name","Name"):
   if r.get(key):known.add(norm(r[key]))
 return known
def records_zip(raw,source,existing,out,limit=0):
 candidates=[];audit={"source_id":source,"sha256":sha(raw),"organization_rows":0,"unique_source_records":0,"new_name_candidates":0,"matched_existing_name":0,"holds":0}
 seen=set()
 with zipfile.ZipFile(io.BytesIO(raw)) as z:
  names=[n for n in z.namelist() if Path(n).name.lower()=="organization.csv"]
  if len(names)!=1:raise ValueError("Expected exactly one organization.csv inside CORDIS archive")
  info=z.getinfo(names[0])
  if info.file_size>350_000_000:raise ValueError("oversized decompressed data")
  with z.open(names[0]) as f:
   text=io.TextIOWrapper(f,encoding="utf-8-sig",errors="replace",newline="")
   sniff=text.readline();text.seek(0)
   dialect=csv.excel
   if "\t" in sniff and ";" not in sniff:dialect=csv.excel_tab
   elif sniff.count(";")>sniff.count(","):dialect="excel-semicolon"\n   if dialect=="excel-semicolon":\n    reader=csv.DictReader(text,delimiter=";")\n   else:\n    reader=csv.DictReader(text,dialect=dialect)
   reader=csv.DictReader(text,dialect=dialect)
   if not reader.fieldnames:raise ValueError("no CSV headers")
   for r in reader:
    audit["organization_rows"]+=1
    name=pick(r,"name","organisationName","organizationName")
    country=pick(r,"country","countryCode")
    ident=pick(r,"organisationID","organizationID","pic")
    project=pick(r,"projectID","projectId")
    if not name: audit["holds"]+=1;continue
    key=(country,norm(name),ident)
    if key in seen:continue
    seen.add(key)
    item={"candidate_id":sha(("cordis:"+source+":"+country+":"+norm(name)+":"+ident).encode()),"display_name":name,"country_raw":country or None,"source_id":source,"source_url":f"https://cordis.europa.eu/project/id/{project}" if project.isdecimal() else None,"source_archive_sha256":audit["sha256"],"source_record_id":ident or None,"first_project_id":project or None,"identity_status":"UNVERIFIED","evidence_layer":"D_LEAD_ONLY","qa_status":"PENDING","publication_approved":False,"matched_existing_name":norm(name) in existing}
    candidates.append(item)
    if item["matched_existing_name"]:audit["matched_existing_name"]+=1
    else:audit["new_name_candidates"]+=1
    if limit and len(candidates)>=limit:break
 audit["unique_source_records"]=len(candidates)
 (out/(source+".jsonl")).write_text("".join(json.dumps(r,ensure_ascii=False)+"\n" for r in candidates),encoding="utf-8")
 return audit
class FeedLinks(HTMLParser):
 def __init__(self):super().__init__();self.links=[]
 def handle_starttag(self,tag,attrs):
  if tag.lower()!="link":return
  d=dict(attrs);mime=d.get("type","").lower()
  if ("rss" in mime or "atom" in mime) and d.get("href"):self.links.append(d["href"])
def discover(out,max_sites,delay):
 found=[];results=[]
 seeds=[(category,url) for category,urls in SEEDS.items() for url in urls]
 for category,url in seeds[:max_sites]:
  record={"category":category,"site":url,"status":"UNKNOWN","feeds":[]}
  try:
   u=urllib.parse.urlparse(url);origin=u.scheme+"://"+u.netloc
   rp=urllib.robotparser.RobotFileParser();rp.set_url(origin+"/robots.txt")
   try:rp.read()
   except Exception:record["status"]="ROBOTS_UNAVAILABLE";results.append(record);continue
   if not rp.can_fetch(UA,url):record["status"]="ROBOTS_BLOCKED";results.append(record);continue
   raw=download(url,1_500_000,timeout=12);parser=FeedLinks();parser.feed(raw.decode("utf-8",errors="ignore"))
   urls=sorted({urllib.parse.urljoin(url,x) for x in parser.links if urllib.parse.urlparse(urllib.parse.urljoin(url,x)).scheme=="https"})
   record["feeds"]=urls;record["status"]="DISCOVERED" if urls else "NO_FEED_TAG"
   for feed in urls:found.append({"category":category,"site":url,"feed":feed,"status":"DISCOVERED_NOT_VALIDATED"})
  except Exception as e:record["status"]="FAILED";record["error"]=type(e).__name__
  results.append(record);time.sleep(delay)
 (out/"feeds.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False)+"\n" for x in found))
 (out/"feed-discovery-report.json").write_text(json.dumps({"seed_sites":len(seeds),"attempted":len(results),"discovered_feeds":len(found),"results":results},indent=2,ensure_ascii=False))
 return len(found)
def main():
 p=argparse.ArgumentParser();p.add_argument("--out-dir",default=".local/harvest");p.add_argument("--notion-jsonl");p.add_argument("--max-download-mb",type=int,default=120);p.add_argument("--programmes",default="cordis-horizon");p.add_argument("--max-candidates",type=int,default=0);p.add_argument("--discover-feeds",action="store_true");p.add_argument("--max-sites",type=int,default=10);p.add_argument("--delay",type=float,default=1.0);p.add_argument("--offline-archive",action="append",default=[],help="SOURCE_ID=/path/to/official.zip");args=p.parse_args()
 out=Path(args.out_dir);out.mkdir(parents=True,exist_ok=True);existing=load_existing(args.notion_jsonl)
 report={"existing_name_keys":len(existing),"archives":[],"feed_count":0,"errors":[],"public_release_count":0}
 sources=dict(ARCHIVES);offline=dict(v.split("=",1) for v in args.offline_archive)
 for source in args.programmes.split(","):
  if source not in sources:report["errors"].append({"source":source,"error":"UNLISTED_SOURCE"});continue
  try:
   raw=Path(offline[source]).read_bytes() if source in offline else download(sources[source],args.max_download_mb*1024*1024,timeout=120)
   report["archives"].append(records_zip(raw,source,existing,out,args.max_candidates))
  except Exception as e:report["errors"].append({"source":source,"error":str(e)[:300]})
 if args.discover_feeds:report["feed_count"]=discover(out,args.max_sites,args.delay)
 (out/"report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
 print(json.dumps(report,indent=2,ensure_ascii=False))
if __name__=="__main__":main()
