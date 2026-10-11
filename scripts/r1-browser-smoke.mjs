import {chromium,devices} from 'playwright';
import fs from 'node:fs';
const out='output/r1-review/browser';fs.mkdirSync(out,{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const results=[];
for(const device of [{name:'desktop',viewport:{width:1440,height:900}},{name:'mobile',...devices['iPhone 13']}]){
 const context=await browser.newContext(device),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try{
  const response=await page.goto('http://127.0.0.1:5174/atlas.html',{waitUntil:'domcontentloaded',timeout:40000});
  await page.locator('#m1-intelligence').waitFor({state:'visible',timeout:30000});
  const feed=await page.locator('#m1-intelligence').innerText();
  const holdExcluded=!feed.includes('CORDIS official project record · SMART · GA 101289409')&&!feed.includes('INTERNAL QA HOLD');
  const dossier=page.locator('#m1-dossier');
  const nav=await page.locator('.topbar .brand').count()===1;
  const map=await page.locator('#map').count()===1;
  const pass=response.status()===200&&holdExcluded&&nav&&map&&feed.includes('No QA-approved intelligence items available yet.')&&!errors.length;
  await page.screenshot({path:out+'/'+device.name+'.png',fullPage:true});
  results.push({device:device.name,pass,http:response.status(),holdExcluded,map,nav,errors});
 }catch(e){results.push({device:device.name,pass:false,errors:[...errors,e.message]})}
 await context.close();
}
await browser.close();fs.writeFileSync(out+'/audit.json',JSON.stringify(results,null,2));
console.log(JSON.stringify(results));if(results.some(r=>!r.pass))process.exit(1);
