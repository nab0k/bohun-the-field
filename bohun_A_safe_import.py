#!/usr/bin/env python3
"""Bohundefence package A: verify feeds, compare Inoreader subscriptions, optionally import.
Dry run by default. Run from repo root (where .env.local lives).
"""
import argparse, csv, json, os, pathlib, sys, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

ap = argparse.ArgumentParser()
ap.add_argument('--csv', default='bohun_sources_A_government_15_audited.csv')
ap.add_argument('--apply', action='store_true', help='Add validated missing feeds (never delete subscriptions)')
a = ap.parse_args()
for line in pathlib.Path('.env.local').read_text(encoding='utf-8').splitlines():
    if '=' in line and not line.lstrip().startswith('#'):
        k,v=line.split('=',1);os.environ.setdefault(k.strip(),v.strip().strip('"\''))
client=os.getenv('INOREADER_APP_ID') or os.getenv('INOREADER_CLIENT_ID')
secret=os.getenv('INOREADER_APP_KEY') or os.getenv('INOREADER_CLIENT_SECRET')
refresh=os.getenv('INOREADER_REFRESH_TOKEN')
if not all((client,secret,refresh)):sys.exit('Missing Inoreader OAuth credentials in .env.local')

def request(url,form=None,token=None):
    data=urllib.parse.urlencode(form).encode() if form is not None else None
    headers={'User-Agent':'Bohundefence-Source-Audit/1.0'}
    if token:headers['Authorization']='Bearer '+token
    if data is not None:headers['Content-Type']='application/x-www-form-urlencoded'
    req=urllib.request.Request(url,data=data,headers=headers,method='POST' if data is not None else 'GET')
    with urllib.request.urlopen(req,timeout=25) as r:return r.read()

def normalize(u):
    p=urllib.parse.urlsplit(u.strip())
    return (p.netloc.lower().removeprefix('www.'),p.path.rstrip('/'),tuple(sorted(urllib.parse.parse_qsl(p.query))))

def valid_feed(url):
    try:
        root=ET.fromstring(request(url))
        tag=root.tag.lower()
        if tag.endswith('rss') or tag.endswith('feed') or tag.endswith('rdf'):
            count=len(root.findall('.//item'))+len(root.findall('{http://www.w3.org/2005/Atom}entry'))
            return count>0, f'XML feed, entries={count}'
        return False,'XML but not RSS/Atom'
    except (ValueError,ET.ParseError,urllib.error.URLError,TimeoutError) as e:
        return False,type(e).__name__+': '+str(e)[:120]

try:
    auth=json.loads(request('https://www.inoreader.com/oauth2/token',{'client_id':client,'client_secret':secret,'refresh_token':refresh,'grant_type':'refresh_token'}))
    token=auth['access_token']
    base='https://www.inoreader.com/reader/api/0/'
    subscriptions=json.loads(request(base+'subscription/list',token=token))['subscriptions']
except Exception as e:sys.exit('Cannot read Inoreader subscriptions: '+str(e))
existing={normalize(s['id'][5:]) for s in subscriptions if s['id'].startswith('feed/')}
rows=list(csv.DictReader(open(a.csv,encoding='utf-8-sig',newline='')))
print('Existing subscriptions:',len(subscriptions),'| candidates:',len(rows))
results=[]
for row in rows:
    name=row['name'];url=row.get('feed_url','').strip()
    if not url:
        print('PENDING',name,'(no verified feed URL)');results.append((name,'PENDING',''));continue
    if normalize(url) in existing:
        print('EXISTS ',name);results.append((name,'EXISTS',url));continue
    ok,detail=valid_feed(url)
    if not ok:
        print('REJECT ',name,detail);results.append((name,'REJECT',url));continue
    print(('IMPORT ' if a.apply else 'READY  '),name,detail)
    if a.apply:
        try:
            response=request(base+'subscription/quickadd',{'quickadd':url},token)
            added=json.loads(response)
            feed_id=added.get('streamId') or added.get('stream_id')
            if not feed_id:
                raise ValueError('quickadd response missing streamId; manual verification required')
            request(base+'subscription/edit',{'s':feed_id,'a':'user/-/label/BOHUN_03_GOVERNMENT','ac':'edit'},token)
            print('  ASSIGNED',feed_id)
            results.append((name,'IMPORTED',url))
        except Exception as e:
            print('  ERROR',name,str(e)[:160]);results.append((name,'ERROR',url))
    else:results.append((name,'READY',url))
with open('bohun_A_import_results.csv','w',newline='',encoding='utf-8') as f:
    w=csv.writer(f);w.writerow(['name','status','feed_url']);w.writerows(results)
print('Report: bohun_A_import_results.csv')
if a.apply:
    try:
        updated=json.loads(request(base+'subscription/list',token=token))['subscriptions']
        print('After import: subscriptions=',len(updated),'BOHUN_03=',sum(any('BOHUN_03_GOVERNMENT' in c.get('label','') for c in s.get('categories',[])) for s in updated))
    except Exception as e:print('Post-import verification failed:',str(e))
