// Cloudflare Pages Function; review-only, never exposes Supabase credentials to browser.
// Requires SUPABASE_URL, SUPABASE_APPROVED_READ_KEY, and approved publication view.
// Do not deploy until the approved-only view and SELECT-only role are independently audited.
const json=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff'}});
const valid=r=>r&&r.qa?.decision==='APPROVE'&&r.qa?.public_release===true&&
 typeof r.content_hash==='string'&&/^[a-f0-9]{64}$/i.test(r.content_hash)&&
 typeof r.source_url==='string'&&/^https:\/\//i.test(r.source_url)&&
 typeof r.headline==='string'&&r.headline.trim().length>0&&r.headline.length<=300&&
 typeof r.published_at==='string'&&!Number.isNaN(Date.parse(r.published_at))&&
 (r.country_iso2==null||/^[A-Z]{2}$/.test(r.country_iso2));
export async function onRequestGet({env}){
 const root=env?.SUPABASE_URL, key=env?.SUPABASE_APPROVED_READ_KEY;
 if(!root||!key) return json({error:'approved feed unavailable'},503);
 let u;try{u=new URL(root);if(u.protocol!=='https:'||u.username||u.password)return json({error:'approved feed unavailable'},503);}catch{return json({error:'approved feed unavailable'},503);}
 const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),7000);
 try{
  const res=await fetch(new URL('/rest/v1/public_approved_intelligence?select=event_id,headline,published_at,country_iso2,source_url,content_hash,takeaway,qa&limit=100',u),{headers:{apikey:key,authorization:'Bearer '+key,accept:'application/json','accept-profile':'publication'},signal:controller.signal});
  if(!res.ok)return json({error:'approved feed unavailable'},503);
  const rows=await res.json();
  if(!Array.isArray(rows)||rows.length>100||!rows.every(valid))return json({error:'approved feed unavailable'},503);
  return json({records:rows});
 }catch{return json({error:'approved feed unavailable'},503);}
 finally{clearTimeout(timeout);}
}
export async function onRequest(){return json({error:'method not allowed'},405);}
