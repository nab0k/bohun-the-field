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
  const feedVisible=await p.locator('#m1-intelligence').isVisible();
  const dossierVisible=await p.locator('#m1-dossier').isVisible();
  const dossierCorrect=device.name==='mobile'?!dossierVisible:dossierVisible;
  const emptyState=(await p.locator('#m1-intelligence').innerText()).includes('No QA-approved intelligence items');
  const signalsHidden=await p.locator('#m1-signals').isHidden();
  const skip=await p.locator('.skip-link').count()===1;
  const noHorizontalOverflow=await p.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+2);
  let mobileNav=true;
  if(device.name==='mobile'){
   const menu=p.locator('.m1-mobile-menu');await menu.locator('summary').click();
   mobileNav=await menu.locator('a[href="/contact.html"]').isVisible();
   await menu.locator('summary').click();
   mobileNav=mobileNav&&await menu.locator('a[href="/contact.html"]').isHidden();
  }
  const basemap=await p.evaluate(()=>({keys:Object.keys(window.__map.getStyle().sources||{}),light:document.querySelector('#look')?.options.length===1,style:window.__map.getStyle()?.name||''}));
  const onlyLight=basemap.light;
  const contact=await p.request.get('http://127.0.0.1:5174/contact.html');
  const contactHtml=await contact.text();
  const contactConfigured=contact.status()===200&&contactHtml.includes('https://formspree.io/f/xbdwvgqp')&&contactHtml.includes('method="POST"')&&contactHtml.includes('name="email"')&&contactHtml.includes('name="message"');
  let nightZoomCheck=false;
  try {
   await p.waitForFunction(()=>!!window.__map.getLayer('night'),{timeout:25000});
   const states=await p.evaluate(async()=>{
    const m=window.__map;
    const wait=()=>new Promise(done=>{const handler=()=>{m.off('idle',handler);done()};m.on('idle',handler);setTimeout(()=>{m.off('idle',handler);done()},1200)});
    m.jumpTo({zoom:1.8});await wait();const far=m.getLayoutProperty('night','visibility');
    m.jumpTo({zoom:5.4});await wait();const near=m.getLayoutProperty('night','visibility');
    m.jumpTo({zoom:1.8});await wait();const back=m.getLayoutProperty('night','visibility');
    return {far,near,back};
   });
   nightZoomCheck=states.far==='visible'&&states.near==='none'&&states.back==='visible';
  }catch(e){errors.push('Night zoom assertion: '+e.message)}
  const style=await p.evaluate(()=>window.__map.getStyle()?.name||'');
  await p.waitForTimeout(1900);await p.screenshot({path:'output/threepane/'+device.name+'.png',fullPage:true});
  const pass=res?.status()===200&&initiallyClosed&&visible&&search&&coverage&&closed&&feedVisible&&dossierCorrect&&emptyState&&signalsHidden&&skip&&noHorizontalOverflow&&mobileNav&&onlyLight&&contactConfigured&&nightZoomCheck&&!errors.length;
  results.push({device:device.name,pass,http:res?.status(),initiallyClosed,visible,search,coverage,closed,feedVisible,dossierVisible,dossierCorrect,emptyState,signalsHidden,skip,noHorizontalOverflow,mobileNav,onlyLight,contactConfigured,nightZoomCheck,style,errors});
 }catch(e){errors.push(e.message);await p.screenshot({path:'output/threepane/'+device.name+'-error.png',fullPage:true}).catch(()=>{});results.push({device:device.name,pass:false,errors});}
 await ctx.close();
}
await browser.close();fs.writeFileSync('output/threepane/audit.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results));
if(results.some(x=>!x.pass))process.exit(1);
