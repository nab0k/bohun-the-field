// P2: existing-source coverage controls, NOT a verified analytics database.
// Company records, geographic polygons and published events have different provenance.
export function coverageSummary({industry,atData,countries,places}){
 const global=(industry?.companies||[]).filter(c=>c.source&&c.name&&/^[A-Z]{2}$/.test(c.country||''));
 const candidate=(atData?.companies||[]).filter(c=>!c.flags?.length&&c.source);
 const ua=(industry?.ukraine||[]).filter(c=>c.name&&c.source);
 const geography=(countries?.features||[]).filter(f=>f.properties?.a3&&f.geometry);
 const capitalPlaces=(places||[]).filter(p=>p.kind==='capital');
 const groups=new Map();
 for(const c of global) groups.set(c.country,(groups.get(c.country)||0)+1);
 return {global, candidate,ua,geography,capitalPlaces,
  globalCountryCounts:[...groups.entries()].sort((a,b)=>a[0].localeCompare(b[0])),
  categories:[...new Set([...global.map(c=>c.product),...candidate.map(c=>c.cat)])].filter(Boolean).sort(),
  approvedAnalytics:0};
}
export function mountCoverageControls({store,industry,atData,countries,places,onCountry,onLayer}){
 const info=coverageSummary({industry,atData,countries,places});
 if(!info.global.length&&!info.candidate.length&&!info.geography.length)return {dispose(){}};
 const root=document.createElement('details');root.id='p2-coverage';root.className='p2-coverage';
 const summary=document.createElement('summary');summary.textContent='Coverage & Filters';root.append(summary);
 const contents=document.createElement('div');contents.className='p2-coverage-body';root.append(contents);
 const label=(text)=>{const n=document.createElement('p');n.textContent=text;contents.append(n);return n;};
 label('GEOGRAPHY: '+info.geography.length+' mapped country geometries. Boundaries do not imply company or analytical coverage.');
 label('SOURCED COMPANIES: '+info.global.length+' global industry entries across '+info.globalCountryCounts.length+' countries; '+info.candidate.length+' unflagged Austria research candidates. Separate lists, not deduplicated.');
 if(info.ua.length)label('UKRAINE: '+info.ua.length+' sourced company names, with no mapped facility coordinates.');
 label('ANALYTICAL EVENTS: only independently QA-approved evidence-backed records may appear in Intelligence.');
 const makeSelect=(title,options,handler)=>{
  const wrap=document.createElement('label');wrap.textContent=title;
  const select=document.createElement('select');select.setAttribute('aria-label',title);
  for(const [value,text] of options){const opt=document.createElement('option');opt.value=value;opt.textContent=text;select.append(opt);}
  select.addEventListener('change',()=>handler(select.value));wrap.append(select);contents.append(wrap);return select;
 };
 const geoOptions=info.geography.map(f=>[f.properties.a3,f.properties.name||f.properties.a3])
  .sort((a,b)=>a[1].localeCompare(b[1]));
 makeSelect('Geographic map view',[['','All map regions'],...geoOptions],a3=>{
  store.set({geographyA3:a3||null,company:null,event:null});
  if(a3)onCountry(a3);
 });
 makeSelect('Industry / category',[['','All sectors'],...info.categories.map(x=>[x,x.replaceAll('-',' ')])],cat=>
   store.setFilters({sectors:cat?[cat]:[]}));
 makeSelect('Source layer',[['world','World geography'],['industry','Global sourced industry'],['companies','Austria research companies'],['sea','Undersea infrastructure']],layer=>{
  store.selectLayer(layer);onLayer(layer);
 });
 const source=document.createElement('p');source.className='source-note';
 source.textContent='Dataset provenance: industry.json, companies-at.json, countries.geojson, places.json. Company lists are not verified contracts or relationships.';
 contents.append(source);
 const container=document.querySelector('#side');
 if(container)container.append(root);else document.body.append(root);
 return {element:root,dispose(){root.remove();},summary:info};
}
