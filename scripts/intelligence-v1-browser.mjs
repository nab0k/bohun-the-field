import {chromium,devices} from 'playwright';
import fs from 'node:fs';
fs.mkdirSync('output/intelligence-v1',{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const results=[];
for(const config of [{name:'desktop',viewport:{width:1440,height:900}},{name:'mobile',...devices['iPhone 13']}]) {
 const context=await browser.newContext(config);const page=await context.newPage();const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try{
  const response=await page.goto('http://127.0.0.1:5174/atlas.html?intelligence=review',{waitUntil:'domcontentloaded',timeout:40000});
  await page.waitForFunction(()=>window.__intelligenceV1Review?.enabled===true,{timeout:45000});
  const status=await page.evaluate(()=>window.__intelligenceV1Review);
  const feed=page.locator('#m1-intelligence'),dossier=page.locator('#m1-dossier');
  const found=await feed.getByRole('button',{name:/Lockheed Martin Australia reports Ferra/}).count()===1;
  await feed.getByRole('button',{name:/Lockheed Martin Australia reports Ferra/}).click();
  const title=await dossier.getByText('Event ID: AU-F35-01').isVisible();
  const evidence=await dossier.getByText('EV-4bf1d6fc59d364948b9d').count()>0;
  const source=await dossier.getByText('Original primary document').getAttribute('href');
  const geo=await page.evaluate(()=>({filter:window.__map.getFilter('intelligence-v1-review-country'),
    selected:window.__bohunM1State.get()}));
  const noPin=await page.locator('.intelligence-event-pin').count()===0;
  const qaHold=(await dossier.innerText()).includes('QA: HOLD');
  const allBlocked=status.blocked.length===2&&status.eligible.length===1;
  await page.screenshot({path:'output/intelligence-v1/'+config.name+'.png',fullPage:true});
  const pass=response?.status()===200&&found&&title&&evidence&&source?.includes('lockheedmartinau.mediaroom.com')&&
    geo.filter?.[2]==='AUS'&&geo.selected.event==='AU-F35-01'&&noPin&&qaHold&&allBlocked&&!errors.length;
  results.push({device:config.name,pass,http:response?.status(),found,title,evidence,source,geo,qaHold,noPin,allBlocked,errors});
 }catch(e){errors.push(e.message);await page.screenshot({path:'output/intelligence-v1/'+config.name+'-error.png',fullPage:true}).catch(()=>{});results.push({device:config.name,pass:false,errors});}
 await context.close();
}
await browser.close();fs.writeFileSync('output/intelligence-v1/audit.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results));if(results.some(r=>!r.pass))process.exit(1);
