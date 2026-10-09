#!/usr/bin/env python3
"""Bohundefence Tender Intelligence v4: TED -> versioned SQLite and CSV."""
import argparse,csv,datetime as dt,json,re,sqlite3,time,urllib.request,urllib.error
from pathlib import Path

URL='https://api.ted.europa.eu/v3/notices/search'
FIELDS=['publication-number','notice-title','buyer-name','buyer-country','publication-date','deadline-receipt-tender-date-lot','total-value','classification-cpv']
QUERIES={
 'UAV':'classification-cpv = 35613000 AND publication-date >= {since}',
 'Defence':'classification-cpv = 35300000 AND publication-date >= {since}',
 'Robotics':'classification-cpv = 42997300 AND publication-date >= {since}',
}
SCHEMA='''CREATE TABLE IF NOT EXISTS tenders (notice_id TEXT PRIMARY KEY, title TEXT, url TEXT, buyer TEXT, country TEXT, published TEXT, deadline_raw TEXT, value_raw TEXT, cpv TEXT, topic TEXT, status TEXT, first_seen TEXT, last_seen TEXT, raw_json TEXT);
CREATE TABLE IF NOT EXISTS tender_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, notice_id TEXT, detected_at TEXT, old_json TEXT, new_json TEXT);
CREATE INDEX IF NOT EXISTS idx_tender_status ON tenders(status);'''

def val(x):
    if isinstance(x,dict):
        for k in ('eng','ENG','en','fra','deu'):
            if k in x:return val(x[k])
        return val(next(iter(x.values()),''))
    if isinstance(x,list):return '; '.join(filter(None,map(val,x)))
    return str(x) if x is not None else ''

def parse_dates(raw):
    # Multiple lots can have different deadlines. Never label open unless ALL parsed deadlines are future.
    dates=[]
    for part in re.split(r';\s*',raw):
        match=re.search(r'\d{4}-\d{2}-\d{2}',part)
        if match:
            try:dates.append(dt.date.fromisoformat(match.group()))
            except ValueError:pass
    return dates

def status(raw):
    dates=parse_dates(raw)
    if not dates:return 'UNKNOWN_VERIFY_NOTICE'
    today=dt.datetime.now(dt.timezone.utc).date()
    if all(d<today for d in dates):return 'DEADLINE_PASSED_VERIFY_NOTICE'
    return 'FUTURE_DEADLINE_VERIFY_NOTICE_TYPE'

def fetch(query,limit):
    body={'query':query,'fields':FIELDS,'limit':limit}
    req=urllib.request.Request(URL,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Accept':'application/json','User-Agent':'BohundefenceResearch/1.0'},method='POST')
    with urllib.request.urlopen(req,timeout=45) as resp: data=json.load(resp)
    if data.get('timedOut'):raise RuntimeError('TED search timed out')
    if not isinstance(data.get('notices'),list):raise RuntimeError('Missing notices list: '+str(list(data)))
    return data

def normalize(n,topic):
    num=val(n.get('publication-number'))
    if not num:return None
    links=n.get('links') or {}
    html=(links.get('html') or {}).get('ENG') or f'https://ted.europa.eu/en/notice/-/detail/{num}'
    deadline=val(n.get('deadline-receipt-tender-date-lot'))
    return dict(notice_id=num,title=val(n.get('notice-title')),url=html,buyer=val(n.get('buyer-name')),country=val(n.get('buyer-country')),published=val(n.get('publication-date')),deadline_raw=deadline,value_raw=val(n.get('total-value')),cpv=val(n.get('classification-cpv')),topic=topic,status=status(deadline))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',default=str(Path(__file__).with_name('tenders.sqlite3')))
    p.add_argument('--out',default=str(Path(__file__).with_name('tenders_v4.csv')))
    p.add_argument('--since',default='20260101',help='Publication date YYYYMMDD')
    p.add_argument('--limit',type=int,default=30,help='Max per query, 1..100')
    p.add_argument('--delay',type=float,default=1.5)
    p.add_argument('--offline-test',action='store_true')
    args=p.parse_args()
    if not re.fullmatch(r'\d{8}',args.since):p.error('--since must be YYYYMMDD')
    try:dt.datetime.strptime(args.since,'%Y%m%d')
    except ValueError:p.error('invalid --since date')
    if not 1<=args.limit<=100:p.error('--limit must be 1..100')
    db=sqlite3.connect(args.db);db.executescript(SCHEMA)
    now=dt.datetime.now(dt.timezone.utc).isoformat()
    inserted=changed=errors=0
    if args.offline_test:
        tests=[{'publication-number':'TEST-1','notice-title':{'eng':'Test UAV'},'deadline-receipt-tender-date-lot':['2099-01-01']}, {'publication-number':'TEST-2','notice-title':{'eng':'Test old'},'deadline-receipt-tender-date-lot':['2020-01-01']}]
        batches=[('OFFLINE_TEST',tests,2)]
    else:
        batches=[]
        for topic,template in QUERIES.items():
            try:
                data=fetch(template.format(since=args.since),args.limit)
                notices=data['notices']
                print(f'TED {topic}: fetched={len(notices)} total={data.get("totalNoticeCount","unknown")}')
                batches.append((topic,notices,data.get('totalNoticeCount')))
            except (urllib.error.URLError,ValueError,RuntimeError,TimeoutError) as exc:
                errors+=1;print(f'TED {topic}: ERROR {exc}')
            time.sleep(args.delay)
    for topic,notices,_ in batches:
        for notice in notices:
            item=normalize(notice,topic)
            if item is None:continue
            nid=item['notice_id'];old=db.execute('SELECT raw_json,first_seen,topic FROM tenders WHERE notice_id=?',(nid,)).fetchone()
            if old and old[2]!=topic:item['topic']='; '.join(sorted(set(old[2].split('; ')+[topic])))
            payload=json.dumps(item,sort_keys=True,ensure_ascii=False)
            if old is None:
                db.execute('INSERT INTO tenders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',tuple(item[k] for k in ('notice_id','title','url','buyer','country','published','deadline_raw','value_raw','cpv','topic','status'))+(now,now,payload));inserted+=1
            else:
                if old[0]!=payload:
                    db.execute('INSERT INTO tender_changes (notice_id,detected_at,old_json,new_json) VALUES (?,?,?,?)',(nid,now,old[0],payload));changed+=1
                db.execute('UPDATE tenders SET title=?,url=?,buyer=?,country=?,published=?,deadline_raw=?,value_raw=?,cpv=?,topic=?,status=?,last_seen=?,raw_json=? WHERE notice_id=?',tuple(item[k] for k in ('title','url','buyer','country','published','deadline_raw','value_raw','cpv','topic','status'))+(now,payload,nid))
        db.commit()
    cols=['notice_id','title','url','buyer','country','published','deadline_raw','value_raw','cpv','topic','status','first_seen','last_seen']
    rows=db.execute('SELECT '+','.join(cols)+' FROM tenders ORDER BY published DESC').fetchall()
    with open(args.out,'w',encoding='utf-8-sig',newline='') as f:
        writer=csv.writer(f);writer.writerow(cols);writer.writerows(rows)
    print(f'DONE stored={len(rows)} new={inserted} changed={changed} errors={errors} csv={args.out}')
    print('Statuses are DEADLINE INDICATORS, NOT confirmed open/closed tenders. Verify notice type and lot details.')
    db.close()
    return 2 if errors else 0
if __name__=='__main__':raise SystemExit(main())
