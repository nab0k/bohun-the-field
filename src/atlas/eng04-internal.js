// ENG-04: local-only internal review surface for the EXISTING MapLibre atlas.
// No keys, no production writes, no model claims, no approved public output.
export function mountAustriaInternalReview({ map, industry }) {
  const args = new URLSearchParams(location.search);
  if (import.meta.env.PROD || args.get('eng04') !== 'internal') return;
  const existing = industry.companies.find(c => c.name === 'Frequentis AG');
  // GitHub atlas currently lacks this company; city centre is a review-only locator,
  // NOT a factory or headquarters address. No changes to industry.json.
  const record = existing || {id:'eng04-frequentis-at-review',name:'Frequentis AG',city:'Vienna',lon:16.3738,lat:48.2082};
  if (!existing) {
    const add = () => {
      if (map.getSource('eng04-vienna-review')) return;
      map.addSource('eng04-vienna-review',{type:'geojson',data:{type:'FeatureCollection',features:[{
        type:'Feature',properties:{status:'INTERNAL_DRAFT',name:'Frequentis AG',geo_role:'city_centroid_not_facility'},
        geometry:{type:'Point',coordinates:[record.lon,record.lat]}
      }]}});
      map.addLayer({id:'eng04-vienna-review-dot',type:'circle',source:'eng04-vienna-review',
        paint:{'circle-radius':10,'circle-color':'#ccae62','circle-stroke-width':3,'circle-stroke-color':'#212626'}});
    };
    if(map.isStyleLoaded()) add(); else map.once('load',add);
  }
  const source = 'https://www.frequentis.com/sites/default/files/pr/2026-05/20260505-pr-Frequentis-BMLV_Flugfunksystem_EN.pdf';
  const panel = document.createElement('aside');
  panel.id = 'eng04-internal';
  panel.setAttribute('aria-label', 'Austria internal analytic review');
  Object.assign(panel.style, {
    position:'absolute', left:'12px', bottom:'18px', zIndex:'900',
    width:'min(350px,calc(100vw - 24px))', maxHeight:'58vh', overflow:'auto',
    padding:'14px', background:'#111a19', color:'#eae9db',
    border:'2px solid #d0a95c', borderRadius:'9px', font:'13px/1.45 system-ui'
  });
  const safe = (tag, txt, parent=panel) => {
    const e=document.createElement(tag);e.textContent=txt;parent.append(e);return e;
  };
  safe('strong','INTERNAL DRAFT / NOT APPROVED');
  safe('p','AUSTRIA · Frequentis AG · 2026-05-05 · issuer-reported contract');
  safe('p','Frequentis says Austria’s defence ministry awarded a programme to modernise military aeronautical radio communications. This has not been independently matched to an award notice.');
  const button=safe('button','View company on the original globe');
  button.type='button';button.style.marginBottom='10px';
  button.addEventListener('click',()=>{
    map.flyTo({center:[record.lon,record.lat],zoom:6.6,duration:850});
  });
  safe('p','Company context: '+record.name+' · Vienna · '+(existing?'existing atlas dataset':'Austria research preview, NOT in current atlas industry.json')+'. Pin is public city centroid, NOT site location. Group/ownership and economic effects not established here.');
  safe('p','Gemini: NOT RUN for this event. Supabase row: NOT VERIFIED. QA-08: PENDING.');
  const a=safe('a','Open primary issuer document');
  a.href=source;a.target='_blank';a.rel='noopener noreferrer';a.style.color='#e9c37b';
  document.body.append(panel);
  window.__eng04_internal_status={mode:'dev_only',qa:'PENDING_QA_08',published:false,supabase:'NOT_VERIFIED',gemini:'NOT_RUN',company_id:record.id,atlas_member:!!existing};
}
