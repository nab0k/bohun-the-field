#!/usr/bin/env python3
"""QA08 CORDIS SMART/HIVE original XML acquisition, first-party links ONLY. No model/billing or DB."""
import urllib.request,urllib.parse,urllib.error,hashlib,datetime,json,re
from html.parser import HTMLParser
from pathlib import Path
class Links(HTMLParser):
 def __init__(self):super().__init__();self.anchor=None;self.out=[]
 def handle_starttag(self,tag,attrs):
  if tag=='a':self.anchor=dict(attrs);self.text=''
 def handle_data(self,data):
  if self.anchor is not None:self.text+=data
 def handle_endtag(self,tag):
  if tag=='a' and self.anchor is not None:self.out.append((self.anchor.get('href',''),self.text.strip()));self.anchor=None
folder=Path('output/cordis-original-xml');folder.mkdir(parents=True,exist_ok=True)
report=[]
for name,id in [('SMART','101289409'),('HIVE','101290048')]:
 url='https://cordis.europa.eu/project/id/'+id;record={'project':name,'grant_agreement_id':id,'page':url,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'BLOCKED_XML_UNAVAILABLE','public_release':False}
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 Bohun QA source reader','Accept':'text/html'})
  with urllib.request.urlopen(req,timeout=22) as resp:raw=resp.read(3000000);record['landing_http']=resp.status
  parser=Links();parser.feed(raw.decode('utf-8','replace'))
  candidates=[]
  for href,label in parser.out:
   resolved=urllib.parse.urljoin(url,href)
   host=urllib.parse.urlsplit(resolved).hostname or ''
   if host!='cordis.europa.eu':continue
   if re.search(r'xml',label+' '+href,re.I):candidates.append(resolved)
  record['discovered_xml_links']=candidates[:12]
  for target in candidates[:5]:
   try:
    with urllib.request.urlopen(urllib.request.Request(target,headers={'User-Agent':'Mozilla/5.0 Bohun QA source reader','Accept':'application/xml,text/xml'}),timeout=24) as resp:
     b=resp.read(5000001);ct=resp.headers.get('Content-Type','');status=resp.status
    if len(b)>5000000:continue
    # do not interpret HTML or unexpected project IDs as first-party XML
    clean=b.lstrip(b'\xef\xbb\xbf \t\r\n')
    if not clean.startswith((b'<?xml',b'<project',b'<cordis',b'<CORDIS')):continue
    if id.encode() not in b:continue
    p=folder/(name.lower()+'-'+id+'.xml');p.write_bytes(b)
    record.update(status='ORIGINAL_XML_CAPTURED',xml_url=target,xml_http=status,xml_content_type=ct,xml_sha256=hashlib.sha256(b).hexdigest(),xml_bytes=len(b),xml_file=p.name)
    break
   except Exception as e:record.setdefault('link_attempt_errors',[]).append(type(e).__name__)
 except Exception as e:record['landing_error_type']=type(e).__name__
 report.append(record)
 print(json.dumps(record,ensure_ascii=False))
(folder/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
assert len(report)==2
