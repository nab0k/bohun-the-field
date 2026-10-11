#!/usr/bin/env python3
"""Bounded RSS/Atom validator. Read-only, no writes to production services."""
import argparse, csv, datetime as dt, html.parser, io, urllib.error, urllib.parse, urllib.request, xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

class Discovery(html.parser.HTMLParser):
    def __init__(self): super().__init__(); self.urls=[]
    def handle_starttag(self, tag, attrs):
        if tag != "link": return
        a=dict(attrs)
        if "alternate" in a.get("rel","").lower().split() and a.get("type","").lower() in ("application/rss+xml","application/atom+xml","application/xml","text/xml") and a.get("href"): self.urls.append(a["href"])

def fetch(url):
    req=urllib.request.Request(url,headers={"User-Agent":"Bohundefence-SourceAudit/0.1 (+public-source-validation)","Accept":"application/rss+xml,application/atom+xml,text/html;q=0.8,*/*;q=0.5"})
    with urllib.request.urlopen(req,timeout=12) as r: return r.status,r.geturl(),r.headers.get("Content-Type",""),r.read(1500000)

def parse_feed(data):
    root=ET.fromstring(data)
    tag=root.tag.rsplit("}",1)[-1].lower()
    if tag not in ("rss","feed","rdf"): return None
    entries=[e for e in root.iter() if e.tag.rsplit("}",1)[-1].lower() in ("item","entry")]
    dates=[]
    for e in entries:
        for child in e.iter():
            if child.tag.rsplit("}",1)[-1].lower() in ("pubdate","published","updated","date"):
                if not child.text: continue
                try:
                    d=parsedate_to_datetime(child.text)
                except Exception:
                    try: d=dt.datetime.fromisoformat(child.text.replace("Z","+00:00"))
                    except Exception: continue
                if d.tzinfo is None: d=d.replace(tzinfo=dt.timezone.utc)
                dates.append(d.astimezone(dt.timezone.utc))
    return len(entries),max(dates).isoformat() if dates else ""

def validate(row):
    result={"status":"ERROR","http_status":"","feed_url":"","latest_publication_utc":"","entries":"","detail":"","checked_at_utc":dt.datetime.now(dt.timezone.utc).isoformat()}
    site=row.get("url","").strip()
    if not site.startswith(("https://","http://")): result["detail"]="invalid_url";return result
    try:
        status,final,ctype,body=fetch(site);result["http_status"]=str(status)
    except Exception as e: result["detail"]=type(e).__name__+":"+str(e)[:120];return result
    candidates=[final]
    if b"<html" in body[:1000].lower() or "text/html" in ctype:
        d=Discovery()
        try: d.feed(body.decode("utf-8","replace"))
        except Exception: pass
        candidates=[urllib.parse.urljoin(final,u) for u in d.urls]
        origin=urllib.parse.urlsplit(final)
        for suffix in ("/feed/","/rss.xml","/feed.xml","/atom.xml"):
            candidates.append(urllib.parse.urlunsplit((origin.scheme,origin.netloc,suffix,"","")))
    seen=set(); failures=[]
    for candidate in candidates[:10]:
        if candidate in seen:continue
        seen.add(candidate)
        if urllib.parse.urlsplit(candidate).hostname != urllib.parse.urlsplit(final).hostname:continue
        try:
            _,feed_url,_,data=fetch(candidate)
            parsed=parse_feed(data)
            if parsed is None:continue
            n,date=parsed
            result.update(status="WORKING" if n else "INACTIVE",feed_url=feed_url,entries=str(n),latest_publication_utc=date,detail="Parsed RSS/Atom; topic relevance requires editorial review")
            return result
        except Exception as e:failures.append(type(e).__name__)
    result.update(status="NO_RSS" if not failures else "ERROR",detail="No parseable RSS/Atom discovered" if not failures else "Feed probes failed: "+",".join(failures[:4]))
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument("--input",required=True);p.add_argument("--output",required=True);p.add_argument("--limit",type=int,default=388);a=p.parse_args()
    with open(a.input,encoding="utf-8-sig",newline="") as f: rows=list(csv.DictReader(f))
    out=[]
    for row in rows[:a.limit]:
        r=validate(row) if row.get("prefilter_status","REVIEW")=="REVIEW" else {"status":"SKIPPED","detail":"Not in REVIEW queue"}
        out.append({**row,**r})
    if not out:raise SystemExit("No input records")
    with open(a.output,"w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for row in out for k in row)),extrasaction="ignore");w.writeheader();w.writerows(out)
    from collections import Counter
    print("STATUS",dict(Counter(x["status"] for x in out)))

if __name__=="__main__":main()
