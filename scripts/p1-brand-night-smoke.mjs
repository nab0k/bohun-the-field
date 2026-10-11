import {chromium,devices} from 'playwright';
import fs from 'node:fs';
fs.mkdirSync('output/p1',{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const results=[];
for(const cfg of [{name:'desktop',viewport:{width:1440,height:900},deviceScaleFactor:1},{name:'mobile',...devices['iPhone 13']}]) {
 const cx=await browser.newContext(cfg),page=await cx.newPage(),errs=[];
 page.on('pageerror',e=>errs.push(e.message));
 try {
  const response=await page.goto('http://127.0.0.1:5174/atlas.html',{waitUntil:'domcontentloaded',timeout:35000});
  await page.locator('.maplibregl-canvas').waitFor({state:'attached',timeout:25000});
  await page.waitForTimeout(3500);
  const brand=await page.locator('.brand-name strong').innerText();
  const subtitle=await page.locator('.brand-name small').innerText();
  const relief=await page.locator('#look option').allTextContents();
  const asset=await page.locator('.brand-logo').evaluate(el=>({complete:el.complete,width:el.naturalWidth}));
  await page.evaluate(()=>window.__map.setZoom(1.8));await page.waitForTimeout(900);
  const far=await page.evaluate(()=>window.__nightVisibility);
  await page.evaluate(()=>window.__map.setZoom(5.4));await page.waitForTimeout(900);
  const near=await page.evaluate(()=>({state:window.__nightVisibility,layout:window.__map.getLayoutProperty('night','visibility')}));
  await page.evaluate(()=>window.__map.setZoom(1.8));await page.waitForTimeout(900);
  const restored=await page.evaluate(()=>window.__nightVisibility);
  await page.screenshot({path:'output/p1/'+cfg.name+'.png',fullPage:true});
  const ok=response?.status()===200&&brand==='Bohun Defence'&&subtitle==='Advisory & Intelligence'
    &&!relief.some(x=>/Relief/i.test(x))&&asset.width>0&&far.effective===true
    &&near.state.effective===false&&near.layout==='none'&&restored.effective===true&&!errs.length;
  results.push({name:cfg.name,ok,brand,subtitle,relief,logoNaturalWidth:asset.width,far,near,restored,errors:errs});
 }catch(e){errs.push(e.message);await page.screenshot({path:'output/p1/'+cfg.name+'-error.png',fullPage:true}).catch(()=>{});results.push({name:cfg.name,ok:false,errors:errs});}
 await cx.close();
}
await browser.close();
fs.writeFileSync('output/p1/audit.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results));
if(results.some(x=>!x.ok))process.exit(1);
