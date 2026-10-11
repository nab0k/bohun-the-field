import {chromium,devices} from 'playwright';import fs from 'node:fs';
fs.mkdirSync('output/p2',{recursive:true});const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const result=[];
for(const c of [{name:'desktop',viewport:{width:1440,height:900}},{name:'mobile',...devices['iPhone 13']}]) {
 const cx=await browser.newContext(c),p=await cx.newPage(),errors=[];p.on('pageerror',e=>errors.push(e.message));
 try{
  const res=await p.goto('http://127.0.0.1:5174/atlas.html',{waitUntil:'domcontentloaded',timeout:40000});
  await p.locator('.maplibregl-canvas').waitFor({state:'attached',timeout:30000});
  const coverage=p.locator('#p2-coverage');
  await coverage.locator('summary').click();
  const words=await coverage.innerText();
  const allRegions=await coverage.locator('select[aria-label="Geographic map view"] option').count();
  await coverage.locator('select[aria-label="Industry / category"]').selectOption('electronics');
  const filters=await p.evaluate(()=>window.__bohunM1State.get().filters.sectors);
  await p.locator('#q').fill('Rheinmetall');
  await p.locator('#q-results button').first().waitFor({state:'attached',timeout:10000});
  const searchResult=await p.locator('#q-results button').first().innerText();
  await p.locator('#q-results button').first().dispatchEvent('mousedown');
  const state=await p.evaluate(()=>window.__bohunM1State.get());
  await p.waitForTimeout(2500);
  await p.screenshot({path:'output/p2/'+c.name+'.png',fullPage:true});
  const hidden=await p.locator('#m1-intelligence').isHidden();
  const pass=res?.status()===200&&allRegions>100&&words.includes('global industry entries')
    &&words.includes('Austria research candidates')&&words.includes('no mapped facility coordinates')
    &&searchResult.includes('Rheinmetall')&&state.country==='DE'&&state.company==='Q161544'
    &&filters.includes('electronics')&&hidden&&!errors.length;
  result.push({name:c.name,pass,http:res?.status(),countryOptions:allRegions,
    globalCoverageText:words.slice(0,350),searchResult:searchResult.slice(0,90),selected:state.company,country:state.country,hiddenAnalytics:hidden,errors});
 }catch(e){errors.push(e.message);await p.screenshot({path:'output/p2/'+c.name+'-error.png',fullPage:true}).catch(()=>{});result.push({name:c.name,pass:false,errors});}
 await cx.close();
}
await browser.close();fs.writeFileSync('output/p2/audit.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
if(result.some(x=>!x.pass))process.exit(1);
