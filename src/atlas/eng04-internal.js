// ENG-04: local-only internal review surface for the EXISTING MapLibre atlas.
// No keys, no production writes, no model claims, no approved public output.
export function mountAustriaInternalReview({ map, industry }) {
  const args = new URLSearchParams(location.search);
  if (import.meta.env.PROD || args.get('eng04') !== 'internal') return;
  const record = industry.companies.find(c => c.name === 'Frequentis AG');
  if (!record) {
    console.warn('ENG-04: Frequentis AG absent from loaded atlas company dataset; demo blocked.');
    return;
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
  safe('p','Company context: '+record.name+' · '+(record.city||'city not provided')+' · atlas industry data. Group/ownership and economic effects not established here.');
  safe('p','Gemini: NOT RUN for this event. Supabase row: NOT VERIFIED. QA-08: PENDING.');
  const a=safe('a','Open primary issuer document');
  a.href=source;a.target='_blank';a.rel='noopener noreferrer';a.style.color='#e9c37b';
  document.body.append(panel);
  window.__eng04_internal_status={mode:'dev_only',qa:'PENDING_QA_08',published:false,supabase:'NOT_VERIFIED',gemini:'NOT_RUN',company_id:record.id};
}
