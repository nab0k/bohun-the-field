// V1 internal source→evidence→claim→event→analysis review on original MapLibre atlas.
// Never mounted in a production bundle. QA HOLD candidates must not be public.
export function mountIntelligenceV1Internal({map,store,packets}){
  if(import.meta.env.PROD||new URLSearchParams(location.search).get('intelligence')!=='review')return null;
  const eligible=packets.filter(p=>p.state==='INTERNAL_CANDIDATE'&&p.source?.content_sha256&&p.evidence?.length&&p.event?.event_id);
  const feed=document.querySelector('#m1-intelligence');
  const dossier=document.querySelector('#m1-dossier');
  if(!feed||!dossier)throw Error('M1 source panels unavailable');
  const originalFeed=feed.innerHTML,originalDossier=dossier.innerHTML;
  const safe=(parent,tag,value)=>{const e=document.createElement(tag);e.textContent=value;parent.append(e);return e};
  const link=(parent,url,text)=>{if(!/^https:\/\//.test(url||''))return;const a=safe(parent,'a',text);a.href=url;a.target='_blank';a.rel='noopener noreferrer'};
  const pane=()=>{feed.hidden=false;dossier.hidden=false;feed.replaceChildren();dossier.replaceChildren()};
  const chosen=new Map(eligible.map(p=>[p.event.event_id,p]));
  // Geography: the published document establishes an Australian programme,
  // not a precise venue/HQ. Highlight a COUNTRY polygon, never fake a pin.
  const layer='intelligence-v1-review-country';
  if(!map.getLayer(layer)){
    map.addLayer({id:layer,type:'line',source:'countries',
      filter:['==',['get','a3'],''],
      paint:{'line-color':'#f2cb72','line-width':3,'line-opacity':0.9}});
  }
  function focus(packet){
    store.selectEvent(packet.event.event_id,null,packet.event.country_iso2);
    pane();safe(feed,'h2','Intelligence Feed · INTERNAL QA HOLD');
    const back=safe(feed,'button','← All review candidates');
    back.type='button';back.addEventListener('click',overview);
    safe(feed,'h3',packet.headline);
    safe(feed,'p',packet.summary);
    safe(dossier,'h2','Event dossier · HOLD');
    safe(dossier,'p','Event ID: '+packet.event.event_id);
    safe(dossier,'p','Source: '+packet.source.publisher);
    safe(dossier,'p','Geo role: '+packet.event.geo_role+' (no event coordinates)');
    safe(dossier,'p','Publisher dateline: '+(packet.event.event_date||'unknown'));
    safe(dossier,'p','Publication date: '+(packet.source.published_at||'unknown'));
    const cl=packet.claims[0];
    safe(dossier,'h3','Attributed claim');
    safe(dossier,'p',cl.statement);
    safe(dossier,'p','Validation: '+cl.independent_validation+' · '+cl.epistemic_status);
    safe(dossier,'p','Evidence: '+cl.evidence_ids.join(', '));
    safe(dossier,'blockquote',packet.evidence[0].exact_excerpt);
    link(dossier,packet.source.original_url,'Original primary document ↗');
    link(dossier,packet.source.snapshot_artifact,'Archived extraction ↗');
    safe(dossier,'p','QA: HOLD · Not published · Not an awarded procurement');
    if(packet.event.geo_role==='COUNTRY_ONLY'){
      map.setFilter(layer,['==',['get','a3'],'AUS']);
      // MapLibre highlight only; do not assign coordinates to the event.
    }
  }
  function overview(){
    pane();map.setFilter(layer,['==',['get','a3'],'']);
    safe(feed,'h2','Intelligence Feed · INTERNAL QA HOLD');
    safe(feed,'p','Research/QA candidates. Not released to the public.');
    for(const packet of eligible){
      const b=safe(feed,'button',packet.headline+' · HOLD');
      b.type='button';b.className='v1-internal-entry';b.addEventListener('click',()=>focus(packet));
    }
    for(const packet of packets.filter(p=>p.state!=='INTERNAL_CANDIDATE')){
      safe(feed,'p',packet.case_id+' · BLOCKED: '+packet.blocker);
    }
    safe(dossier,'h2','Company & event dossier');
    safe(dossier,'p','Choose a candidate to inspect original evidence. No claims approved for publication.');
  }
  overview();
  window.__intelligenceV1Review={enabled:true,eligible:eligible.map(p=>p.case_id),
    blocked:packets.filter(p=>p.state!=='INTERNAL_CANDIDATE').map(p=>p.case_id),publication:false};
  return {focus,overview};
}
