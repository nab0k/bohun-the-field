import { chromium } from 'playwright';
import fs from 'node:fs';
const dir='output/eng04';fs.mkdirSync(dir,{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const page=await browser.newPage({viewport:{width:1440,height:900},deviceScaleFactor:1});
const errors=[],consoleErrors=[];
page.on('pageerror',e=>errors.push(String(e)));
page.on('console',m=>{if(m.type()==='error')consoleErrors.push(m.text())});
let status='FAIL',panel='',atlas='';
try {
 const response=await page.goto('http://127.0.0.1:5174/atlas.html?eng04=internal',{waitUntil:'domcontentloaded',timeout:45000});
 atlas=await page.title();
 await page.locator('#eng04-internal').waitFor({state:'visible',timeout:45000});
 panel=await page.locator('#eng04-internal').innerText();
 if(response?.status()!==200)throw Error('HTTP '+response?.status());
 if(!panel.includes('INTERNAL DRAFT / NOT APPROVED'))throw Error('missing QA draft label');
 if(!panel.includes('Supabase row: NOT VERIFIED'))throw Error('missing Supabase warning');
 if(!panel.includes('Gemini: NOT RUN'))throw Error('missing Gemini warning');
 if(!await page.locator('#map').count())throw Error('original map container missing');
 await page.locator('.maplibregl-canvas').waitFor({state:'attached',timeout:25000});
 await page.screenshot({path:dir+'/austria-internal.png',fullPage:true});
 await page.getByRole('button',{name:'View company on the original globe'}).click();
 status='PASS';
} catch(e){errors.push(String(e));await page.screenshot({path:dir+'/austria-error.png',fullPage:true}).catch(()=>{});}
const output={status,atlas,page_errors:errors,console_errors:consoleErrors.slice(0,30),panel_excerpt:panel.slice(0,500),screenshot:status==='PASS'?'austria-internal.png':'austria-error.png'};
fs.writeFileSync(dir+'/browser-result.json',JSON.stringify(output,null,2));
await browser.close();
console.log(JSON.stringify(output));
if(status!=='PASS'||errors.length)process.exit(1);
