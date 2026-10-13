import assert from 'node:assert/strict';
import {onRequestGet} from '../functions/api/intelligence/approved.js';
const unavailable=await onRequestGet({env:{}});
assert.equal(unavailable.status,503);
assert.deepEqual(await unavailable.json(),{error:'approved feed unavailable'});
const insecure=await onRequestGet({env:{SUPABASE_URL:'http://localhost',SUPABASE_APPROVED_READ_KEY:'unused'}});
assert.equal(insecure.status,503);
const good={event_id:'event-a',headline:'Verified source update',published_at:'2026-10-13T06:00:00Z',source_url:'https://example.org/item',content_hash:'a'.repeat(64),qa:{decision:'APPROVE',public_release:true},country_iso2:'AT'};
const originalFetch=globalThis.fetch;
try {
  let calls=0;
  globalThis.fetch=async (url,opts)=>{
    calls++;
    assert.equal(new URL(url).hostname,'db.example.org');
    assert.equal(opts.headers.apikey,'read-only-test-key');
    assert.equal(opts.headers['accept-profile'],'publication');
    assert.equal(new URL(url).pathname,'/rest/v1/public_approved_intelligence');
    return new Response(JSON.stringify([good]),{status:200,headers:{'content-type':'application/json'}});
  };
  let r=await onRequestGet({env:{SUPABASE_URL:'https://db.example.org',SUPABASE_APPROVED_READ_KEY:'read-only-test-key'}});
  assert.equal(r.status,200);
  assert.deepEqual((await r.json()).records,[good]);
  assert.equal(calls,1);
  for(const bad of [
    {...good,qa:{decision:'HOLD',public_release:false}},
    {...good,qa:{decision:'REJECT',public_release:false}},
    {...good,qa:{decision:'APPROVE',public_release:false}},
    {...good,content_hash:null},
    {...good,source_url:'http://invalid.example'}
  ]){
    globalThis.fetch=async()=>new Response(JSON.stringify([bad]),{status:200});
    r=await onRequestGet({env:{SUPABASE_URL:'https://db.example.org',SUPABASE_APPROVED_READ_KEY:'read-only-test-key'}});
    assert.equal(r.status,503,'Unapproved backend data must fail closed');
  }
  globalThis.fetch=async()=>new Response('backend error',{status:500});
  r=await onRequestGet({env:{SUPABASE_URL:'https://db.example.org',SUPABASE_APPROVED_READ_KEY:'read-only-test-key'}});
  assert.equal(r.status,503);
} finally {globalThis.fetch=originalFetch;}
console.log('approved endpoint: successful projection, HOLD, REJECT, non-public and failure cases passed');
