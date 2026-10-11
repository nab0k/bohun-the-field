import {chromium,devices} from 'playwright';import fs from 'node:fs';
fs.mkdirSync('output/threepane',{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const results=[];
for(const device of [{name:'desktop',viewport:{width:1440,height:900}},{name:'mobile',...devices['iPhone 13']}]) {
 const ctx=await browser.newContext(device),p=await ctx.newPage(),errors=[];
 p.on('pageerror',e=>errors.push(e.message));
 try{
  const res=await p.goto('http://127.0.0.1:5174/atlas.html',{waitUntil:'domcontentloaded',timeout:35000});
  await p.locator('.maplibregl-canvas').waitFor({state:'attached',timeout:30000});
  const btn=p.locator('#nav-drawer-toggle'),drawer=p.locator('#side');
  const initiallyClosed=await drawer.getAttribute('aria-hidden')==='true';
  await btn.click();
  const visible=await drawer.isVisible()&&await btn.getAttribute('aria-expanded')==='true';
  const search=await drawer.locator('input#q').isVisible();
  await drawer.locator('#p2-coverage').waitFor({state:'attached',timeout:30000});
  const coverage=await drawer.locator('#p2-coverage').count()===1;
  await p.keyboard.press('Escape');
  const closed=await drawer.getAttribute('aria-hidden')==='true'&&await btn.getAttribute('aria-expanded')==='false';
  const panelsHidden=await p.locator('#m1-intelligence').isHidden()&&await p.locator('#m1-dossier').isHidden()&&await p.locator('#m1-signals').isHidden();
  const style=await p.evaluate(()=>window.__map.getStyle()?.name||'');
  await p.waitForTimeout(1900);await p.screenshot({path:'output/threepane/'+device.name+'.png',fullPage:true});
  const pass=res?.status()===200&&initiallyClosed&&visible&&search&&coverage&&closed&&panelsHidden&&!errors.length;
  results.push({device:device.name,pass,http:res?.status(),initiallyClosed,visible,search,coverage,closed,panelsHidden,style,errors});
 }catch(e){errors.push(e.message);await p.screenshot({path:'output/threepane/'+device.name+'-error.png',fullPage:true}).catch(()=>{});results.push({device:device.name,pass:false,errors});}
 await ctx.close();
}
await browser.close();fs.writeFileSync('output/threepane/audit.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results));
if(results.some(x=>!x.pass))process.exit(1);
