import { chromium, devices } from 'playwright';
import fs from 'node:fs';
const dir='output/m1';fs.mkdirSync(dir,{recursive:true});
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const result={cases:[],version:'m1-state-v1',public_release:false};
for(const cfg of [{label:'desktop',viewport:{width:1440,height:900},deviceScaleFactor:1},{label:'mobile',...devices['iPhone 13']}]) {
 const context=await browser.newContext(cfg),page=await context.newPage(),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 try{
  const response=await page.goto('http://127.0.0.1:5174/atlas.html',{waitUntil:'domcontentloaded',timeout:40000});
  await page.locator('.maplibregl-canvas').waitFor({state:'attached',timeout:30000});
  await page.waitForTimeout(6000); // map tiles/rendering, not merely canvas creation
  const visibleAnalytics=await page.locator('#m1-intelligence').isVisible();
  const navVisible=cfg.label==='mobile'?await page.locator('.m1-mobile-menu > summary').isVisible():await page.locator('.topbar > nav a[href="/about-project.html"]').isVisible();
  if(cfg.label==='mobile') {await page.locator('.m1-mobile-menu > summary').click();if(!await page.getByRole('link',{name:'ABOUT PROJECT'}).last().isVisible())throw Error('M1 mobile menu link not visible');await page.locator('.m1-mobile-menu > summary').click();}
  await page.screenshot({path:dir+'/'+cfg.label+'-m1.png',fullPage:true});
  const companyState=await page.evaluate(()=>{const s=window.__bohunM1State;s.selectCompany('at-041','AT');return s.get()});
  const good=response?.status()===200&&!visibleAnalytics&&navVisible&&companyState.company==='at-041'&&companyState.country==='AT'&&!errors.length;
  result.cases.push({label:cfg.label,status:good?'PASS':'FAIL',http:response?.status(),emptyAnalyticsHidden:!visibleAnalytics,navVisible,companyState:companyState.company,country:companyState.country,errors});
 }catch(e){errors.push(e.message);await page.screenshot({path:dir+'/'+cfg.label+'-error.png',fullPage:true}).catch(()=>{});result.cases.push({label:cfg.label,status:'FAIL',errors});}
 await context.close();
}
await browser.close();result.status=result.cases.every(x=>x.status==='PASS')?'PASS':'FAIL';
fs.writeFileSync(dir+'/m1-browser-audit.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));if(result.status!=='PASS')process.exit(1);
