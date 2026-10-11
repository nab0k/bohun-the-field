#!/usr/bin/env python3
"""Read-only best-effort snapshot of existing PUBLIC web resources.
NOT a Cloudflare Pages/DNS/Workers or Formspree configuration backup.
"""
from urllib.request import Request,urlopen
from urllib.parse import urljoin,urlparse
from html.parser import HTMLParser
import hashlib,json,re,os,zipfile,datetime
from pathlib import Path
HOST="bohundefence.com"
ORIGIN="https://"+HOST+"/"
ROOT=Path("output/m0-existing-site");ROOT.mkdir(parents=True,exist_ok=True)
seen=set();queue=[ORIGIN];manifest=[];form_actions=[]
class Links(HTMLParser):
    def __init__(self): super().__init__();self.refs=[]
    def handle_starttag(self,tag,attrs):
        x=dict(attrs)
        if tag=="form" and x.get("action"):form_actions.append(urljoin(self.base,x["action"]))
        if tag in ("a","link","script","img","source"):
            for key in ("href","src"):
                if x.get(key):self.refs.append(urljoin(self.base,x[key]))
for iteration in range(120):
    if not queue:break
    uri=queue.pop(0)
    if uri in seen:continue
    seen.add(uri)
    u=urlparse(uri)
    if u.hostname not in (HOST,"www."+HOST) or u.scheme!="https":continue
    if u.query or u.fragment:continue
    try:
        req=Request(uri,headers={"User-Agent":"Bohundefence-M0-prechange-backup/1.0","Accept":"*/*"})
        with urlopen(req,timeout=16) as r:
            body=r.read(5_000_000)
            final=r.geturl()
            mime=r.headers.get("Content-Type","")
            code=r.status
        if urlparse(final).hostname not in (HOST,"www."+HOST):continue
        path=u.path.strip("/") or "index.html"
        if path.endswith("/"):path+="index.html"
        if ".." in Path(path).parts:continue
        dest=ROOT/"files"/path
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_bytes(body)
        manifest.append({"uri":uri,"path":str(dest.relative_to(ROOT)),"http_status":code,"bytes":len(body),
            "sha256":hashlib.sha256(body).hexdigest(),"content_type":mime})
        if "text/html" in mime:
            parser=Links();parser.base=uri;parser.feed(body.decode("utf-8",errors="replace"))
            for link in parser.refs:
                p=urlparse(link)
                if p.hostname in (HOST,"www."+HOST) and link not in seen and link not in queue:queue.append(link)
        if "text/css" in mime:
            for raw in re.findall(r'url\\(([^)]+)\\)',body.decode('utf-8',errors='replace')):
                link=urljoin(uri,raw.strip().strip('\"').strip("'"))
                if urlparse(link).hostname in (HOST,"www."+HOST) and link not in queue and link not in seen:queue.append(link)
    except Exception as e:
        manifest.append({"uri":uri,"error_type":type(e).__name__})
status={"snapshot_utc":datetime.datetime.now(datetime.timezone.utc).isoformat(),"original_site":ORIGIN,
 "scope":"public HTTP representation only; NOT Cloudflare/DNS/Workers/Formspree account rollback",
 "items":manifest,"form_actions":sorted(set(form_actions)),
 "root_saved":any(x.get("path")=="files/index.html" for x in manifest),
 "account_config_backup_verified":False,"rollback_verified":False}
(ROOT/"manifest.json").write_text(json.dumps(status,ensure_ascii=False,indent=2))
with zipfile.ZipFile("output/m0-existing-site-snapshot.zip","w",zipfile.ZIP_DEFLATED) as z:
    for p in ROOT.rglob("*"):
        if p.is_file():z.write(p,p.relative_to(ROOT.parent))
print(f"M0 old public snapshot: items={len(manifest)}, root_saved={status['root_saved']}, rollback_verified=False")
if not status["root_saved"]:exit(1)
