// M1 single-source UI state for the existing Claude MapLibre atlas.
// Inert by default: data panels render ONLY when approved real records exist.
const initial=Object.freeze({country:null,company:null,event:null,filters:{sectors:[],types:[],query:''},period:{from:null,to:null},layer:'companies'});
const validIso=v=>v===null||/^[A-Z]{2}$/.test(v);
export function createAtlasState(seed={}){
 let state={...initial,...seed,filters:{...initial.filters,...seed.filters},period:{...initial.period,...seed.period}};
 const listeners=new Set();
 const copy=()=>structuredClone(state);
 const set=patch=>{
  const next={...state,...patch,filters:{...state.filters,...(patch.filters||{})},period:{...state.period,...(patch.period||{})}};
  if(!validIso(next.country)) throw Error('Invalid country ISO2');
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
 return records.filter(r=>r?.qa?.decision==='APPROVE'&&r?.qa?.public_release===true&&r?.source_url&&r?.content_hash
   &&(!state.country||r.country_iso2===state.country)
   &&(!state.company||r.company_id===state.company)
   &&(!state.event||r.event_id===state.event)
   &&(!state.period.from||r.published_at>=state.period.from)
   &&(!state.period.to||r.published_at<=state.period.to)
   &&(!state.filters.types?.length||state.filters.types.includes(r.event_type))
   &&(!state.filters.sectors?.length||state.filters.sectors.includes(r.sector)));
}
export function mountM1Panels({store,records=[]}){
 const root=document.createElement('section');root.id='m1-intelligence';root.hidden=true;root.setAttribute('aria-label','Verified intelligence');
 root.style.cssText='position:absolute;top:92px;right:12px;z-index:650;max-width:330px;max-height:60vh;overflow:auto;background:#111b18;color:#f3eddb;padding:14px;border:1px solid #8d8867;border-radius:8px';
 document.body.append(root);
 const link=(href,label,parent)=>{const a=document.createElement('a');a.href=href;a.target='_blank';a.rel='noopener noreferrer';a.textContent=label;parent.append(a);};
 const render=state=>{
  root.replaceChildren();const rows=selectApproved(records,state);root.hidden=rows.length===0;
  if(!rows.length)return;
  const heading=document.createElement('h2');heading.textContent='Intelligence · Verified';root.append(heading);
  for(const r of rows.slice(0,20)){
   const card=document.createElement('article'),h=document.createElement('h3'),p=document.createElement('p');
   h.textContent=r.headline;p.textContent=r.takeaway;card.append(h,p);link(r.source_url,'Primary source',card);root.append(card);
  }
 };
 const unsubscribe=store.subscribe(render);
 return {root,dispose(){unsubscribe();root.remove();}};
}
