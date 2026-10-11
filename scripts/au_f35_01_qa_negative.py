#!/usr/bin/env python3
"""ENG-02: audit one AU-F35-01 model output, without approving or publishing it."""
import json, pathlib, re
folder=pathlib.Path('output/au-f35-01')
summary=json.loads((folder/'run_summary.json').read_text())
cardpath=folder/'gemini_response.json'
context=json.loads((folder/'input_context.json').read_text())
negatives={
'T1_MENTORING_NOT_F35_PROCUREMENT': [r'new (?:f-35|f35).{0,45}(?:contract|award)',r'2026.{0,30}procurement contract'],
'T2_LM_DOES_NOT_OWN_FERRA': [r'lockheed martin (?:owns|acquired|controls) ferra'],
'T3_NO_STRATEGY_CAUSATION': [r'strategy of denial (?:required|caused|mandated) (?:this |the )?(?:mentoring|selection)'],
'T4_NO_UNIVERSAL_TIER1': [r'ferra (?:is|was) (?:a )?tier[ -]?1 (?:supplier|contractor)'],
'T5_NO_F35_SOLE_STRATEGY': [r'f-35a? (?:is|was) the sole (?:implementation|means|platform)'],
'T6_UNKNOWN_CONTROL_NOT_NEGATIVE': [r'(?:ferra|lockheed).{0,70}(?:has no chinese|no china control)'],
'T7_72_ONLY_AS_OF_2024': [r'(?<!as of )(?:currently|in 2026) (?:has|operates) 72 f-35']
}
checks={}
if not cardpath.exists():
 for key in negatives:checks[key]={'status':'NOT_RUN','reason':'NO_GEMINI_RESPONSE'}
 summary['qa']['reasons'].append('SEVEN_NEGATIVE_CONTROLS_NOT_RUN')
else:
 try:
  obj=json.loads(cardpath.read_text());body=json.dumps(obj,ensure_ascii=False).lower()
  for k,patterns in negatives.items():
   bad=any(re.search(p,body,re.I) for p in patterns)
   checks[k]={'status':'FLAGGED_FOR_QA' if bad else 'HEURISTIC_PASS_ONLY','verified_by_source':False}
  claims=obj.get('claims',[])
  badquotes=[{'source_id':x.get('source_id'),'claim':x.get('statement')} for x in claims
     if x.get('source_id') not in context['source_excerpts'] or not x.get('exact_quote')
     or x['exact_quote'] not in context['source_excerpts'][x['source_id']]]
  summary['qa']['quote_match_failures']=badquotes
  if badquotes:summary['qa']['reasons'].append('NONLITERAL_QUOTE')
  summary['qa']['reasons'].append('RULE_BASED_NEGATIVES_NOT_INDEPENDENT_EVIDENCE_REVIEW')
 except Exception as e:
  for key in negatives:checks[key]={'status':'UNPARSEABLE_GEMINI_OUTPUT','error_type':type(e).__name__}
  summary['qa']['reasons'].append('UNPARSEABLE_MODEL_CARD')
summary['qa']['negative_controls']=checks
summary['qa']['decision']='HOLD';summary['scope']['published_cards']=0
(folder/'qa_negative_controls.json').write_text(json.dumps(checks,indent=2))
(folder/'run_summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps({'gemini':summary['gemini'],'qa_decision':summary['qa']['decision'],
 'negative_controls':{k:v['status'] for k,v in checks.items()}},ensure_ascii=False))
