// M1 single-source UI state for the existing Claude MapLibre atlas.
// Inert by default: data panels render ONLY when approved real records exist.
const initial=Object.freeze({country:null,geographyA3:null,company:null,event:null,filters:{sectors:[],types:[],query:''},period:{from:null,to:null},layer:'companies'});
const validIso=v=>v===null||/^[A-Z]{2}$/.test(v);
export function createAtlasState(seed={}){
 let state={...initial,...seed,filters:{...initial.filters,...seed.filters},period:{...initial.period,...seed.period}};
 const listeners=new Set();
 const copy=()=>structuredClone(state);
 const set=patch=>{
  const next={...state,...patch,filters:{...state.filters,...(patch.filters||{})},period:{...state.period,...(patch.period||{})}};
  if(!validIso(next.country)) throw Error('Invalid country ISO2');
  if(next.geographyA3!==null && !/^[A-Z]{3}$/.test(next.geographyA3)) throw Error('Invalid geography A3');
  if(next.period.from&&next.period.to&&next.period.from>next.period.to) throw Error('Invalid period');
  next.filters.sectors=[...new Set(next.filters.sectors)];
  next.filters.types=[...new Set(next.filters.types)];
  state=next;
  listeners.forEach(fn=>fn(copy()));
  return copy();
 };
 return Object.freeze({get:copy,set,subscribe(fn){listeners.add(fn);fn(copy());return()=>listeners.delete(fn);},selectCountry:country=>set({country,company:null,event:null}),selectCompany:(company,country)=>set({company,country:country||state.country,event:null}),selectEvent:(event,company,country)=>set({event,company:company??state.company,country:country??state.country}),setPeriod:(from,to)=>set({period:{from,to}}),setFilters:filters=>set({filters}),selectLayer:layer=>set({layer})});
}
export function selectApproved(records,state){
 if(!Array.isArray(records))return[];
 return records.filter(r=>r?.qa?.decision==='APPROVE'&&r?.qa?.public_release===true&&/^https:\/\//.test(r?.source_url||'')&&r?.content_hash
   &&(!state.country||r.country_iso2===state.country)
   &&(!state.company||r.company_id===state.company)
   &&(!state.event||r.event_id===state.event)
   &&(!state.period.from||r.published_at>=state.period.from)
   &&(!state.period.to||r.published_at<=state.period.to)
   &&(!state.filters.types?.length||state.filters.types.includes(r.event_type))
   &&(!state.filters.sectors?.length||state.filters.sectors.includes(r.sector)));
}
export function mountM1Panels({store,records=[]}){
  const create=(id,title,left)=>{
    const box=document.createElement('aside');
    box.id=id;box.hidden=true;box.setAttribute('aria-label',title);box.setAttribute('role','region');
    box.style.cssText='position:fixed;z-index:650;'+(left?'left:14px;bottom:25px;':'right:14px;top:90px;')+
      'width:min(310px,calc(100vw - 28px));max-height:58vh;overflow:auto;background:#111b18;color:#f3eddb;padding:14px;border:1px solid #8d8867;border-radius:8px';
    document.body.append(box);return box;
  };
  const feed=create('m1-intelligence','Verified Intelligence',false);
  const dossier=create('m1-dossier','Company evidence dossier',true);
  dossier.hidden=false;
  const signals=create('m1-signals','Evidence-backed signals',false);
  signals.style.top='auto';signals.style.bottom='25px';
  const text=(node,type,value)=>{const child=document.createElement(type);child.textContent=value;node.append(child);return child;};
  const link=(node,url)=>{
    const a=document.createElement('a');a.href=url;a.textContent='Primary source';
    a.target='_blank';a.rel='noopener noreferrer';node.append(a);
  };
  const render=state=>{
    for(const box of [feed,dossier,signals]){box.replaceChildren();box.hidden=true;}
    feed.hidden=false;
    text(feed,'h2','Intelligence Feed');
    const allowed=selectApproved(records,state);
    if(!allowed.length){
      dossier.hidden=window.matchMedia('(max-width:760px)').matches;
      text(dossier,'h2','Company & event dossier');
      text(dossier,'p','Select a verified map object to inspect its available sources. No analytical dossier is approved for this selection.');
      text(feed,'p','No QA-approved intelligence items available yet. Research candidates and AI drafts are not published.');
      if(!window.matchMedia('(max-width:760px)').matches)text(feed,'p','The map may contain geographic and separately sourced company data; absence from the feed does not mean there are no companies.');
      return;
    }
    text(feed,'p','Verified, source-linked intelligence only.');
    for(const item of allowed.slice(0,15)){
      const entry=document.createElement('article');
      const title=document.createElement('button');title.type='button';title.textContent=item.headline;
      title.addEventListener('click',()=>store.selectEvent(item.event_id,item.company_id,item.country_iso2));
      entry.append(title);text(entry,'p',item.takeaway||'');
      link(entry,item.source_url);feed.append(entry);
    }
    const focused=state.company?allowed.filter(r=>r.company_id===state.company):[];
    if(focused.length){
      dossier.hidden=false;text(dossier,'h2','Company · Evidence Dossier');
      text(dossier,'p',focused[0].company_name||state.company);
      text(dossier,'p',String(focused.length)+' source-backed approved item(s)');
      for(const item of focused.slice(0,10))link(dossier,item.source_url);
    }
    const verifiedSignals=allowed.filter(r=>r.signal===true&&r.qa?.risk_certified===true);
    if(verifiedSignals.length){
      signals.hidden=false;text(signals,'h2','Signals · Verified');
      for(const item of verifiedSignals.slice(0,10)){text(signals,'p',item.headline);link(signals,item.source_url);}
    }
  };
  const disposeSubscription=store.subscribe(render);
  return {feed,dossier,signals,dispose(){disposeSubscription();for(const box of [feed,dossier,signals])box.remove();}};
}
