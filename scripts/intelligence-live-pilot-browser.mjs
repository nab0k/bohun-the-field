import {chromium,devices} from 'playwright';import fs from 'node:fs';
fs.mkdirSync('output/live-pilot/browser',{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});const results=[];
for(const device of [{name:'desktop',viewport:{width:1440,height:900}},{name:'mobile',...devices['iPhone 13']}]){
 const ctx=await browser.newContext(device),p=await ctx.newPage(),errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 try{
  const response=await p.goto('http://127.0.0.1:5174/atlas.html?pilot=internal',{waitUntil:'domcontentloaded',timeout:40000});
  await p.waitForFunction(()=>window.__livePilot01?.from_db,{timeout:35000});
  const button=p.locator('#m1-intelligence button.v1-internal-entry');
  await button.click();
  const dossier=await p.locator('#m1-dossier').innerText();
  const feed=await p.locator('#m1-intelligence').innerText();
  const state=await p.evaluate(()=>({pilot:window.__livePilot01,selected:window.__bohunM1State.get(),nightEnabled:!!window.__nightVisibility}));
  const pass=response.status()===200&&state.pilot.count===1&&state.selected.event==='event:grant:cordis:101289409'&&
    state.pilot.contract==='bohun-intelligence-vertical-slice-v1'&&state.pilot.evidence===2&&state.pilot.claims===2&&
    dossier.includes('Persisted raw_item ID: 1')&&dossier.includes('QA: HOLD')&&
    dossier.includes('Publication candidate: publication:source-update:cordis:101289409:v1')&&
    dossier.includes('Evidence links: 2 claims / 2 exact XML locators')&&
    feed.includes('LIVE SOURCE PILOT')&&feed.includes('GA 101289409 · HOLD')&&
    dossier.includes('Geo role: COORDINATOR_COUNTRY_ONLY')&&dossier.includes('project.euc_contribution: 992606.6')&&dossier.includes('project.ec_signature_date: 2026-06-22')&&!errors.length;
  await p.screenshot({path:'output/live-pilot/browser/'+device.name+'.png',fullPage:true});
  results.push({device:device.name,pass,http:response.status(),pilot:state.pilot,selected:state.selected.event,errors});
 }catch(e){errors.push(e.message);results.push({device:device.name,pass:false,errors});}
 await ctx.close();
}
await browser.close();fs.writeFileSync('output/live-pilot/browser/audit.json',JSON.stringify(results,null,2));
console.log(JSON.stringify(results));if(results.some(r=>!r.pass))process.exit(1);
