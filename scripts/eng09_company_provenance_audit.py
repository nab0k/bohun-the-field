#!/usr/bin/env python3
"""ENG09: read-only, no publication, audit exactly what original atlas datasets prove."""
import json, pathlib, collections, urllib.parse, datetime
root=pathlib.Path('public/atlas')
industry=json.loads((root/'industry.json').read_text())
at=json.loads((root/'companies-at.json').read_text())
out=pathlib.Path('output/eng09');out.mkdir(parents=True,exist_ok=True)
def source_class(url):
 host=urllib.parse.urlparse(url or '').hostname or ''
 if host.endswith(('wikidata.org','wikipedia.org')):return 'TERTIARY_WIKIDATA_WIKIPEDIA'
 if host.endswith(('.gov.au','.gov.uk','.gov','wko.at','advantageaustria.org')):return 'REGISTRY_ASSOCIATION_GOVERNMENT_CANDIDATE'
 if host:return 'OTHER_WEB_UNVERIFIED'
 return 'MISSING'
counts=collections.Counter(); records=[]
for row in industry.get('companies',[]):
 typ=source_class(row.get('source'));counts[(row.get('country','?'),typ)]+=1
 records.append({'company_id':row.get('id'),'country_iso2':row.get('country'),'name_as_in_source':row.get('name'),
  'public_city_as_claimed':row.get('city'),'source_url':row.get('source'),'website':row.get('web'),
  'source_class':typ,'legal_identity_verified':False,'hq_city_verified':False,'map_position_verified':False,
  'qa':'HOLD_RESEARCH_CANDIDATE','reason':'No original registrar/manufacturer/government passage and version/hash independently pinned'})
for row in at.get('companies',[]):
 typ=source_class(row.get('source'));counts[('AT',typ)]+=1
 records.append({'company_id':row.get('id'),'country_iso2':'AT','name_as_in_source':row.get('name'),
  'public_city_as_claimed':row.get('city'),'source_url':row.get('source'),'website':row.get('web'),
  'source_class':typ,'legal_identity_verified':False,'hq_city_verified':False,'map_position_verified':False,
  'qa':'EXCLUDED_FLAGGED' if row.get('flags') else 'HOLD_RESEARCH_CANDIDATE',
  'flags':row.get('flags',[]),'reason':'Original atlas source listing only; official legal identity and independent HQ passage not verified'})
summary={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'nature':'Source provenance audit only, NOT a reviewed company registry or map release',
 'input':['public/atlas/industry.json','public/atlas/companies-at.json'],
 'global_company_candidates':len(industry.get('companies',[])),
 'austria_research_candidates':len(at.get('companies',[])),
 'austria_flagged_excluded':sum(bool(c.get('flags')) for c in at.get('companies',[])),
 'distinct_global_countries':sorted(set(c.get('country') for c in industry.get('companies',[]) if c.get('country'))),
 'by_country_and_source_class':[{'country':c,'source_class':t,'count':n} for (c,t),n in sorted(counts.items())],
 'independently_primary_verified':0,
 'publication_decision':'HOLD_FOR_PRIMARY_SOURCE_PASSAGE_AND_LEGAL_ENTITY_QA',
 'working_db_writes':0}
(out/'candidate_provenance.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
(out/'coverage_audit.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False))
assert summary['global_company_candidates']==72
assert summary['austria_research_candidates']==50
assert summary['austria_flagged_excluded']==9
