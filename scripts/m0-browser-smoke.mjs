import {chromium} from 'playwright';
import fs from 'node:fs';
const dir='output/m0';fs.mkdirSync(dir,{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const page=await browser.newPage({viewport:{width:1440,height:900}});
const errors=[];page.on('pageerror',e=>errors.push(String(e)));
let result={status:'FAIL',checks:{},errors:[]};
try{
 const base='http://127.0.0.1:5175';
 const response=await page.goto(base+'/',{waitUntil:'domcontentloaded',timeout:30000});
 result.checks.index200=response?.status()===200;
 await page.locator('.maplibregl-canvas').waitFor({state:'attached',timeout:35000});
 result.checks.mapCanvas=true;
 result.checks.journeysHidden=await page.locator('#open-need').isHidden()&&await page.locator('#open-have').isHidden()&&await page.locator('#entry').isHidden();
 result.checks.liveHidden=await page.locator('#live').isHidden();
 result.checks.scaleDemoHidden=await page.locator('#scale').isHidden();
 await page.screenshot({path:dir+'/m0-atlas.png',fullPage:true});
 for(const slug of ['about','services','about-project','support-project','contact','privacy']){
    const r=await page.goto(base+'/'+slug+'.html',{waitUntil:'domcontentloaded',timeout:20000});
    result.checks[slug]=r?.status()===200 && await page.locator('main h1').count()===1;
 }
 await page.goto(base+'/contact.html');
 result.checks.formspreePreserved=await page.locator('form').getAttribute('action')==='https://formspree.io/f/xbdwvgqp' && await page.locator('form input[name=email]').count()===1;
  const data=await page.evaluate(async()=>await (await fetch('/atlas/companies-at.json')).json());
 result.checks.atFlagsExcluded=Array.isArray(data.companies)&&data.companies.every(c=>!c.flags?.length);
 result.checks.atFixtureRetained=data.companies.some(c=>c.id==='at-041'&&c.name==='Frequentis AG');
 result.checks.noUAAddresses=data.companies.every(c=>c.country_iso2!=='UA'||(!c.hq_address&&!c.lat));
 result.status=Object.values(result.checks).every(Boolean)&&!errors.length?'PASS':'FAIL';
}catch(e){errors.push(String(e));}
result.errors=errors;
fs.writeFileSync(dir+'/m0-browser-result.json',JSON.stringify(result,null,2));
await browser.close();console.log(JSON.stringify(result));
if(result.status!=='PASS')process.exit(1);
