#!/usr/bin/env python3
"""AU-F35-01 evidence and bounded Gemini QA experiment. No DB writes or publication."""
import os,json,hashlib,urllib.request,urllib.error,datetime,re,html
from pathlib import Path
O=Path('output/au-f35-01');O.mkdir(parents=True,exist_ok=True)
urls={
'K1':'https://www.defence.gov.au/about/strategic-planning/2024-national-defence-strategy-2024-integrated-investment-program',
'K2':'https://www.defence.gov.au/news-events/releases/2024-12-19/final-f-35a-aircraft-delivered',
'K3':'https://www.defence.gov.au/defence-activities/projects/joint-strike-fighter-aircraft',
'K4':'https://www.exportfinance.gov.au/customer-stories/ferra-engineering/',
'K5':'https://ferra-group.com/about-us/projects/',
'K6':'https://lockheedmartinau.mediaroom.com/index.php?item=122738&s=2429',
'K7':'https://abr.business.gov.au/ABN/View/30008425509',
'K8':'https://abr.business.gov.au/ABN/View/88060996465'
}
audit={'fixture':'AU-F35-01','at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'mode':'READ_ONLY_NO_PUBLICATION','urls':{},'gemini':{'attempted':False,'status':'NOT_RUN','usage':None},
 'qa':{'decision':'HOLD','claim_level':'NOT_ENOUGH_INFO','reasons':['SOURCE_SNAPSHOTS_AND_QA_NOT_YET_SIGNED']},
 'scope':{'db_writes':0,'published_cards':0,'migrations':0}}
excerpts={}
for key,url in urls.items():
 info={'url':url}
 try:
  req=urllib.request.Request(url,headers={'User-Agent':'Bohundefence-AU-F35-evidence/1.0','Accept':'text/html,application/pdf'})
  with urllib.request.urlopen(req,timeout=18) as response:
   raw=response.read(240000);info['http_status']=response.status
   info['content_type']=response.headers.get('Content-Type','')
  info['sha256_sample']=hashlib.sha256(raw).hexdigest();info['sample_bytes']=len(raw)
  if raw.startswith(b'%PDF'):
   info['extractable']=False;info['error_type']='PDF_REQUIRES_TEXT_EXTRACTION'
  else:
   text=re.sub(r'(?is)<script.*?</script>|<style.*?</style>',' ',raw.decode('utf-8','replace'))
   text=html.unescape(re.sub('<[^>]+>',' ',text));text=' '.join(text.split())
   excerpts[key]=text[:3500];info['extractable']=len(text)>150
 except urllib.error.HTTPError as e:info['error_type']='HTTPError';info['http_status']=e.code
 except Exception as e:info['error_type']=type(e).__name__
 audit['urls'][key]=info
# Exact provenance only; research fixture claims are prompts, not source-confirmed until matched against snapshots.
prompt={'question':'What actually changed when Ferra was selected for the 12-month Lockheed Martin Australia mentor-protege initiative, and what can be established about historic F-35 work and the Australian NDS 2024?',
 'required':{'actors':['LOCKHEED MARTIN AUSTRALIA PTY LIMITED ABN 30008425509','FERRA ENGINEERING PTY. LTD. ABN 88060996465'],
 'event_vs_publish_date':'separate or null','claim_evidence':'K1-K8 source IDs and EXACT passages',
 'output':'100-180 word analytical capsule; distinguish fact/attribution/inference/unknown; never infer award, ownership, universal Tier1 or doctrine causation'},
 'source_excerpts':excerpts}
(O/'input_context.json').write_text(json.dumps(prompt,ensure_ascii=False,indent=2))
missing=[k for k in urls if not audit['urls'][k].get('extractable')]
if missing:audit['qa']['reasons'].append('INCOMPLETE_EXTRACTABLE_EVIDENCE:'+','.join(missing))
# Never infer that presence of a key implies a free quota. Owner or quota evidence must enable explicitly.
if os.getenv('GEMINI_FREE_QUOTA_VERIFIED')!='1':
 audit['gemini']['status']='BLOCKED_FREE_QUOTA_NOT_VERIFIED'
 audit['qa']['reasons'].append('GEMINI_FREE_QUOTA_UNVERIFIED')
elif not os.getenv('GEMINI_API_KEY'):
 audit['gemini']['status']='BLOCKED_MISSING_SECRET'
 audit['qa']['reasons'].append('GEMINI_API_KEY_MISSING')
elif missing:
 audit['gemini']['status']='BLOCKED_INCOMPLETE_SOURCE_CONTEXT'
else:
 model=os.getenv('GEMINI_MODEL','gemini-2.5-flash')
 audit['gemini'].update({'attempted':True,'model':model})
 instruction='Return a strict JSON object {capsule,claims:[{statement,source_id,exact_quote,verdict}],event_date,publish_date,not_established}. Use only input source excerpts. Never infer contracts or ownership.'
 data=json.dumps({'contents':[{'parts':[{'text':instruction+'\\n'+json.dumps(prompt)}]}],
                  'generationConfig':{'responseMimeType':'application/json','temperature':0,'maxOutputTokens':1200}}).encode()
 req=urllib.request.Request('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent',data=data,headers={'Content-Type':'application/json','x-goog-api-key':os.environ['GEMINI_API_KEY']})
 try:
  with urllib.request.urlopen(req,timeout=55) as response: answer=json.load(response)
  audit['gemini']['status']='RESPONSE_RECEIVED'
  audit['gemini']['usage']=answer.get('usageMetadata')
  value=answer['candidates'][0]['content']['parts'][0]['text']
  (O/'gemini_response.json').write_text(value)
  card=json.loads(value)
  bad=[]
  for x in card.get('claims',[]):
   key=x.get('source_id')
   if key not in excerpts or not x.get('exact_quote') or x['exact_quote'] not in excerpts[key]:bad.append(key or 'MISSING')
  audit['qa']['quote_match_failures']=bad
  if bad:audit['qa']['reasons'].append('UNSUPPORTED_OR_NONLITERAL_QUOTES')
  audit['qa']['reasons'].append('QA_08_HUMAN_REVIEW_PENDING')
 except urllib.error.HTTPError as e:
  audit['gemini'].update(status='HTTP_ERROR',http_status=e.code)
  audit['qa']['reasons'].append('GEMINI_HTTP_'+str(e.code))
 except Exception as e:
  audit['gemini'].update(status='ERROR',error_type=type(e).__name__)
  audit['qa']['reasons'].append('GEMINI_OUTPUT_ERROR')
(O/'run_summary.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2))
print('AU-F35-01',json.dumps({'snapshots':{k:('HTTP'+str(v.get('http_status'))) if 'http_status' in v else v.get('error_type') for k,v in audit['urls'].items()},'gemini':audit['gemini'],'qa':audit['qa']}))
