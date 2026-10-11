#!/usr/bin/env python3
"""Read-only original AU-F35 K6 event text provenance; never call a model."""
import re,hashlib,json,datetime,html,urllib.request
from html.parser import HTMLParser
from pathlib import Path
class Visible(HTMLParser):
 def __init__(self):super().__init__();self.skip=0;self.parts=[]
 def handle_starttag(self,tag,attrs):
  if tag in ("script","style"):self.skip+=1
  if tag in ("p","h1","h2","h3","div","article","time"):self.parts.append("\n")
 def handle_endtag(self,tag):
  if tag in ("script","style"):self.skip=max(0,self.skip-1)
  if tag in ("p","h1","h2","h3","div","article","time"):self.parts.append("\n")
 def handle_data(self,data):
  if not self.skip:self.parts.append(data)
url="https://lockheedmartinau.mediaroom.com/index.php?item=122738&s=2429"
out=Path("output/eng09-k6");out.mkdir(parents=True,exist_ok=True)
report={"url":url,"checked_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"policy":"READ_ONLY_HOLD"}
try:
 req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0 (compatible; Bohundefence source QA read-only)"})
 with urllib.request.urlopen(req,timeout=25) as res:
  body=res.read(1000001);report["http_status"]=res.status;report["content_type"]=res.headers.get("Content-Type")
 if len(body)>1000000:raise ValueError("DOCUMENT_TOO_LARGE")
 report["document_sha256"]=hashlib.sha256(body).hexdigest()
 p=Visible();p.feed(body.decode("utf-8","replace"))
 txt=html.unescape(" ".join(p.parts))
 txt=re.sub(r"\s+"," ",txt).strip()
 report["visible_text_length"]=len(txt)
 hits=list(re.finditer(r"Ferra|mentor.prot.g.|F.35",txt,re.I))
 report["matching_mentions"]=len(hits)
 report["passages"]=[]
 for m in hits[:15]:
  a=max(0,m.start()-140);b=min(len(txt),m.end()+230)
  frag=txt[a:b]
  report["passages"].append({"start":a,"end":b,"sha256":hashlib.sha256(frag.encode()).hexdigest(),"text":frag})
 report["extraction_quality"]="MATCHES_FOUND" if hits else "NO_EVENT_KEYWORD_IN_VISIBLE_TEXT"
 report["qa_decision"]="HOLD"
except Exception as e:
 report["error_type"]=type(e).__name__;report["qa_decision"]="HOLD"
(out/"k6_source_extract.json").write_text(json.dumps(report,indent=2,ensure_ascii=False))
print(json.dumps({k:v for k,v in report.items() if k!="passages"}))
