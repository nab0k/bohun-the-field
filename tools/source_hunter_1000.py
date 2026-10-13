#!/usr/bin/env python3
"""Bounded public RSS discovery; no authenticated APIs or production writes."""
import argparse,csv,hashlib,json,os,re,time,urllib.parse,urllib.robotparser
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path
import requests,feedparser
from bs4 import BeautifulSoup

SEEDS=[
"https://www.defense.gov","https://www.darpa.mil","https://www.diu.mil",
"https://www.nato.int","https://eda.europa.eu","https://defence-industry-space.ec.europa.eu",
"https://www.gov.uk/government/organisations/ministry-of-defence",
"https://www.canada.ca/en/department-national-defence.html",
"https://www.defence.gov.au","https://www.nzdf.mil.nz","https://www.mod.go.jp",
"https://www.dapa.go.kr","https://www.drdo.gov.in","https://www.mindef.gov.sg",
"https://www.defence.govt.nz","https://www.armyrecognition.com",
"https://www.defensenews.com","https://breakingdefense.com","https://www.twz.com",
"https://www.janes.com","https://www.navalnews.com","https://www.airandspaceforces.com",
"https://www.defence-industry.eu","https://euro-sd.com","https://www.sipri.org",
"https://www.iiss.org","https://www.csis.org","https://www.rand.org",
"https://www.rusi.org","https://www.aspistrategist.org.au","https://www.defence24.com",
"https://www.defenseone.com","https://www.c4isrnet.com","https://www.shephardmedia.com",
"https://www.army-technology.com","https://www.airforce-technology.com",
"https://www.naval-technology.com","https://www.edrmagazine.eu",
"https://www.bundeswehr.de","https://www.bmvg.de","https://www.defense.gouv.fr",
"https://www.difesa.it","https://www.defensa.gob.es","https://www.gov.pl/web/national-defence",
"https://www.fmn.dk","https://www.forsvaret.no","https://www.forsvarsmakten.se",
"https://www.defensie.nl","https://www.mil.be","https://www.bmlv.gv.at",
"https://www.mo.gov.cz","https://www.mod.gov.sk","https://www.mod.gov.ua",
"https://www.mil.gov.ua","https://www.mnd.gov.tw","https://www.mod.gov.il",
"https://www.mod.gov.ae","https://www.mod.gov.sa","https://www.mod.gov.qa",
"https://www.mod.gov.om","https://www.mod.gov.bh","https://www.mindef.gov.bn",
"https://www.mod.gov.my","https://www.kemhan.go.id","https://www.dnd.gov.ph",
"https://www.defence.gov.za","https://www.defenceweb.co.za",
"https://www.ssb.gov.tr","https://www.aselsan.com","https://www.saab.com",
"https://www.baesystems.com","https://www.rheinmetall.com","https://www.thalesgroup.com",
"https://www.leonardo.com","https://www.airbus.com","https://www.lockheedmartin.com",
"https://www.northropgrumman.com","https://www.rtx.com","https://www.boeing.com",
"https://www.anduril.com","https://www.palantir.com","https://www.l3harris.com",
"https://www.general-dynamics.com","https://www.hensoldt.net","https://www.kongsberg.com",
"https://www.naval-group.com","https://www.mbda-systems.com",
"https://www.ted.europa.eu","https://www.nato.int/cps/en/natohq/topics_49201.htm",
"https://www.eur-lex.europa.eu","https://www.edf.europa.eu",
"https://www.horizon-europe.gouv.fr","https://www.eda.europa.eu",
"https://www.defenceiq.com","https://www.dsei.co.uk","https://www.eurosatory.com",
"https://www.idexuae.ae","https://www.singaporeairshow.com",
"https://www.ausdefence.com.au","https://www.defense-aerospace.com",
"https://www.flightglobal.com","https://www.aviationweek.com",
"https://www.technologyreview.com","https://techcrunch.com/category/security/",
"https://www.defenceconnect.com.au","https://www.defence-industry.com",
"https://www.asiapacificdefencereporter.com","https://www.thedefensepost.com"]
HINTS=["/feed","/rss","/rss.xml","/feed.xml","/atom.xml","/news/rss","/news/feed","/category/defense/feed"]
KEYS=("defen","defen","militar","aerospace","security","naval","army","weapon","space","dual-use","procurement","technology","innovation","industry","news","press","research")
def norm(u):
 p=urllib.parse.urlsplit(u.strip());return urllib.parse.urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/") or "/",p.query,""))
def host(u):return urllib.parse.urlsplit(u).hostname or ""
def allowed(url,cache):
 h=host(url)
 if h not in cache:
  rp=urllib.robotparser.RobotFileParser()
  try:rp.set_url("https://"+h+"/robots.txt");rp.read();cache[h]=rp
  except Exception:cache[h]=None
 return cache[h] is not None and cache[h].can_fetch("BohunSourceDiscovery/1.0",url)
def get(session,url,robots):
 if not allowed(url,robots):return None
 try:
  r=session.get(url,timeout=(4,8),allow_redirects=True)
  if r.status_code!=200 or len(r.content)>2000000:return None
  return r
 except requests.RequestException:return None
def seed_urls():
 out=set(SEEDS)
 for path in ["data","collectors","output","sources"]:
  root=Path(path)
  if not root.exists():continue
  for f in list(root.rglob("*.csv"))[:80]:
   if f.stat().st_size>3000000:continue
   try:
    for row in csv.DictReader(f.open(encoding="utf-8-sig",errors="replace")):
     for k,v in row.items():
      if k and v and any(t in k.lower() for t in ("url","website","domain","link")):
       if v.startswith("http"):out.add(v)
       elif re.fullmatch(r"[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}",v):out.add("https://"+v)
   except Exception:pass
  for f in list(root.rglob("*.opml"))[:40]:
   try:
    s=BeautifulSoup(f.read_text(errors="replace"),"xml")
    for tag in s.find_all("outline"):
     for attr in ("htmlUrl","xmlUrl"):
      if tag.get(attr):out.add(tag[attr])
   except Exception:pass
 return sorted(u for u in out if u.startswith(("https://","http://")))
def scan(url):
 sess=requests.Session();sess.headers.update({"User-Agent":"BohunSourceDiscovery/1.0 (+public feed discovery)"})
 robots={}
 r=get(sess,url,robots)
 if r is None:return {"site":url,"status":"unavailable_or_robots","feeds":[],"related":[]}
 soup=BeautifulSoup(r.text[:1000000],"html.parser")
 cand=set()
 for link in soup.select('link[rel]'):
  if "alternate" in (link.get("rel") or []):
   typ=link.get("type","")
   if any(x in typ for x in ("rss","atom","json")) and link.get("href"):cand.add(urllib.parse.urljoin(r.url,link["href"]))
 for suffix in HINTS[:5]:cand.add(urllib.parse.urljoin(r.url,suffix))
 if any(k in url.lower() for k in KEYS):pass
 related=[]
 for a in soup.select("a[href]"):
  link=urllib.parse.urljoin(r.url,a["href"]);p=urllib.parse.urlsplit(link)
  if p.scheme not in ("http","https"):continue
  if host(link)==host(r.url):
   if any(x in (a.get_text(" ",strip=True)+" "+p.path).lower() for x in ("rss","feed","news","press","media","blog")):cand.add(link)
  elif len(related)<12 and any(x in (a.get_text(" ",strip=True)+" "+link).lower() for x in KEYS):related.append(link)
 feeds=[]
 for link in list(cand)[:20]:
  resp=get(sess,link,robots)
  if resp is None:continue
  parsed=feedparser.parse(resp.content)
  if parsed.version and (parsed.entries or parsed.feed.get("title")):
   feeds.append({"feed_url":norm(resp.url),"title":parsed.feed.get("title",""),"entries_seen":len(parsed.entries),"site":url})
 return {"site":url,"status":"scanned","feeds":feeds,"related":related}
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--minutes",type=float,default=19);ap.add_argument("--target",type=int,default=1000);ap.add_argument("--workers",type=int,default=10);a=ap.parse_args()
 deadline=time.monotonic()+a.minutes*60;out=Path("output/source-hunter");out.mkdir(parents=True,exist_ok=True)
 queue=seed_urls();seen=set();records=[];feeds={};cycle=0
 while queue and time.monotonic()<deadline-20 and len(feeds)<a.target:
  batch=[]
  while queue and len(batch)<a.workers*2:
   u=queue.pop(0);h=host(u)
   if not h or h in seen:continue
   seen.add(h);batch.append(u)
  if not batch:break
  with ThreadPoolExecutor(max_workers=a.workers) as pool:
   futs={pool.submit(scan,u):u for u in batch}
   for fut in as_completed(futs):
    try:r=fut.result()
    except Exception as e:r={"site":futs[fut],"status":"error:"+type(e).__name__,"feeds":[],"related":[]}
    records.append({"site":r["site"],"status":r["status"],"feed_count":len(r["feeds"])})
    for f in r["feeds"]:feeds.setdefault(f["feed_url"],f)
    for link in r["related"]:
     if host(link) not in seen and len(queue)<10000:queue.append(link)
  cycle+=1
  print(json.dumps({"cycle":cycle,"sites":len(records),"feeds":len(feeds),"queue":len(queue)}),flush=True)
 with (out/"sources.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=["site","status","feed_count"]);w.writeheader();w.writerows(records)
 with (out/"validated-feeds.csv").open("w",newline="") as f:
  w=csv.DictWriter(f,fieldnames=["feed_url","title","entries_seen","site"]);w.writeheader();w.writerows(feeds.values())
 import xml.etree.ElementTree as ET
 op=ET.Element("opml",version="2.0");body=ET.SubElement(op,"body");ET.SubElement(op,"head").append(ET.Element("title"))
 for f in feeds.values():ET.SubElement(body,"outline",{"type":"rss","text":f["title"] or host(f["feed_url"]),"xmlUrl":f["feed_url"],"htmlUrl":f["site"]})
 ET.ElementTree(op).write(out/"new-sources.opml",encoding="utf-8",xml_declaration=True)
 report={"target":a.target,"validated_unique_feeds":len(feeds),"sites_checked":len(records),"queued_remaining":len(queue),"deadline_minutes":a.minutes,"production_writes":0,"inoreader_subscriptions_added":0}
 (out/"report.json").write_text(json.dumps(report,indent=2))
 print(json.dumps(report),flush=True)
if __name__=="__main__":main()
