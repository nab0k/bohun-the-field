#!/usr/bin/env python3
"""Discover official feeds for all 15 package-A organisations. Read-only.
Run from project directory with audited CSV. No credentials required.
"""
import csv, urllib.request, urllib.parse, urllib.error, xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

INPUT='bohun_sources_A_government_15_audited.csv'
OUTPUT='bohun_A_discovery_results.csv'
HEADERS={'User-Agent':'Mozilla/5.0 (compatible; BohundefenceSourceAudit/1.0)'}
class Links(HTMLParser):
 def __init__(self): super().__init__(); self.links=[]
 def handle_starttag(self,tag,attrs):
  if tag!='link':return
  a=dict(attrs)
  if any(t in a.get('type','').lower() for t in ('rss+xml','atom+xml')) and a.get('href'):self.links.append(a['href'])
def get(url):
 req=urllib.request.Request(url,headers=HEADERS)
 with urllib.request.urlopen(req,timeout=15) as r:return r.read(),r.geturl()
def validate(url):
 try:
  raw,final=get(url); root=ET.fromstring(raw); tag=root.tag.lower()
  if not any(tag.endswith(x) for x in ('rss','feed','rdf')):return False,'not RSS/Atom',0,final
  count=len(root.findall('.//item'))+len(root.findall('{http://www.w3.org/2005/Atom}entry'))
  return count>0,'valid XML',count,final
 except Exception as e:return False,f'{type(e).__name__}: {str(e)[:110]}',0,''
rows=list(csv.DictReader(open(INPUT,encoding='utf-8-sig',newline='')))
results=[]
for row in rows:
 name=row['name'];site=row['official_url']; candidates=[]
 if row.get('feed_url'):candidates.append(('existing',row['feed_url']))
 if 'gov.uk/government/organisations/' in site:
  slug=site.rstrip('/').split('/')[-1]
  candidates.append(('govuk_atom_candidate','https://www.gov.uk/search/news-and-communications.atom?'+urllib.parse.urlencode({'organisations[]':slug})))
 try:
  html,final=get(site);p=Links();p.feed(html.decode('utf-8','replace'))
  candidates.extend(('html_autodiscovery',urllib.parse.urljoin(final,x)) for x in p.links)
  page_status='accessible'
 except Exception as e:page_status=f'{type(e).__name__}: {str(e)[:85]}'
 seen=set();accepted=[];checks=[]
 for origin,url in candidates:
  if url in seen:continue
  seen.add(url);ok,msg,count,final=validate(url)
  checks.append(f'{origin}: {url} [{msg}; entries={count}]')
  if ok:accepted.append(url)
 status='VALID_FEED' if accepted else ('CANDIDATE_NOT_VALIDATED' if candidates else 'NO_FEED_DISCOVERED')
 results.append({'country':row['country'],'name':name,'official_url':site,'status':status,'validated_feed_urls':' | '.join(accepted),'website_status':page_status,'checks':' | '.join(checks),'bohun_folder':row['bohun_folder']})
 print(f'{name}: {status}; {len(accepted)} valid feeds')
with open(OUTPUT,'w',newline='',encoding='utf-8') as f:
 w=csv.DictWriter(f,fieldnames=results[0].keys());w.writeheader();w.writerows(results)
print('Wrote',OUTPUT)
