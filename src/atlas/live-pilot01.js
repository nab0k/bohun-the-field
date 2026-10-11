// Live Pilot01. Dev-only internal view reads export produced from persisted D1-schema SQLite.
// Does not claim an official event location or a QA-approved analysis.
export async function mountLivePilot01({map,store}){
 if(import.meta.env.PROD||new URLSearchParams(location.search).get('pilot')!=='internal')return null;
 const r=await fetch('/atlas/pilot-internal-feed.json',{cache:'no-store'});
 if(!r.ok)throw Error('PILOT_PERSISTED_READBACK_NOT_AVAILABLE');
 const feed=await r.json();
 if(!feed.internal_only||feed.qa_status!=='HOLD')throw Error('PILOT_FAIL_CLOSED');
 const left=document.getElementById('m1-intelligence'),right=document.getElementById('m1-dossier');
 const add=(host,tag,value)=>{const e=document.createElement(tag);e.textContent=value;host.append(e);return e};
 function show(row){
   store.selectEvent(row.event_id,row.company_id,row.country_iso2);
   right.replaceChildren();right.hidden=false;
   add(right,'h2','Source dossier · INTERNAL HOLD');
   add(right,'p','Source ID: '+row.source_id);
   add(right,'p','Original hash: '+row.source_sha256);
   add(right,'p','Persisted raw_item ID: '+row.raw_item_id);
   add(right,'p','Geo role: '+row.geo_role+' · No coordinates or map pin asserted');
   add(right,'p','Company: not independently bound');
   add(right,'p','AI claim: none; pre-existing Gemini fixtures are not source proof');
   add(right,'p','QA: HOLD · This is not an APPROVE publication');
   const a=add(right,'a','Original official CORDIS XML ↗');a.href=row.source_url;a.rel='noopener noreferrer';a.target='_blank';
 }
 left.replaceChildren();left.hidden=false;
 add(left,'h2','Intelligence Feed · LIVE SOURCE PILOT');
 add(left,'p','Internal source captures from a restarted persistent staging record; not approved public intelligence.');
 for(const item of feed.items||[]){
  if(item.qa?.decision!=='HOLD'||item.qa?.public_release!==false)throw Error('PILOT_QA_GATE');
  const button=add(left,'button',item.headline+' · HOLD');
  button.className='v1-internal-entry';button.type='button';button.addEventListener('click',()=>show(item));
 }
 window.__livePilot01={from_db:true,db_ids:feed.items.map(x=>x.raw_item_id),count:feed.items.length,qa:'HOLD',published:false};
 return window.__livePilot01;
}
