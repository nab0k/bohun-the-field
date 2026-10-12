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
  await page.screenshot({path:out+'/'+device.name+'-feed-map-dossier.png',fullPage:true});
  // Existing navigation and Night Side controls must stay available.
  if(device.name==='desktop'){
    const night=page.locator('#night');await night.click();await page.screenshot({path:out+'/desktop-night-side.png'});
    await night.click();
    const companies=page.locator('#companies');await companies.click();
    await page.screenshot({path:out+'/desktop-companies-layer.png'});
  }else{
    const menu=page.locator('.m1-mobile-menu summary');await menu.click();
    if(!await page.locator('.m1-mobile-menu').evaluate(el=>el.open))throw Error('MOBILE_NAV_NOT_OPEN');
    await page.screenshot({path:out+'/mobile-navigation.png'});
  }
  const contactResponse=await page.goto('http://127.0.0.1:5174/contact.html',{waitUntil:'domcontentloaded'});
  if(contactResponse.status()!==200)throw Error('CONTACT_PAGE_HTTP_'+contactResponse.status());
  const form=page.locator('form');if(!await form.count())throw Error('CONTACT_FORM_MISSING');
  const formTarget=await form.getAttribute('action');if(!formTarget?.startsWith('https://formspree.io/'))throw Error('CONTACT_ACTION_UNEXPECTED');
  const required=await form.locator('[required]').count();if(required<3)throw Error('CONTACT_REQUIRED_FIELDS_MISSING');
  // No form submission, no network writes.
  await page.screenshot({path:out+'/'+device.name+'-contact.png'});

  results.push({device:device.name,pass,http:response.status(),holdExcluded,map,nav,errors});
 }catch(e){results.push({device:device.name,pass:false,errors:[...errors,e.message]})}
 await context.close();
}
await browser.close();fs.writeFileSync(out+'/audit.json',JSON.stringify(results,null,2));
console.log(JSON.stringify(results));if(results.some(r=>!r.pass))process.exit(1);
