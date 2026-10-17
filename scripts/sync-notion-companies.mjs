// Export a complete, fail-closed snapshot of reviewed Notion companies.
// Run with NOTION_TOKEN in GitHub Actions. No token or review fields in public data.
import {writeFile,mkdir} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';

const DATA_SOURCE=process.env.NOTION_COMPANIES_DATA_SOURCE || 'ec8c6312-3bfc-48e8-9a27-3c5827e72fda';
const OUTPUT=process.env.NOTION_COMPANIES_OUTPUT || 'public/atlas/notion-companies.json';
const token=process.env.NOTION_TOKEN;
if(!token)throw Error('NOTION_TOKEN missing; refusing to replace snapshot');
const headers={Authorization:'Bearer '+token,'Notion-Version':'2025-09-03','Content-Type':'application/json'};
const textValue=(p)=>p?.type==='title'||p?.type==='rich_text'?(p[p.type]||[]).map(x=>x.plain_text||'').join(''):null;
const option=p=>p?.select?.name||null;
const url=p=>p?.url||null;
const checkbox=p=>p?.checkbox===true;
const exported=new Map();
let cursor=null,pages=0,all=0;
do {
 const body={page_size:100};if(cursor)body.start_cursor=cursor;
 const response=await fetch('https://api.notion.com/v1/data_sources/'+encodeURIComponent(DATA_SOURCE)+'/query',{
  method:'POST',headers,body:JSON.stringify(body)});
 if(!response.ok)throw Error('Notion query HTTP '+response.status+'; refusing partial export');
 const data=await response.json();
 if(!Array.isArray(data.results))throw Error('Invalid Notion pagination response');
 for(const page of data.results){
  all++;
  if(page.object!=='page'||page.archived||page.in_trash)continue;
  const p=page.properties||{};
  const company={
   notion_url:page.url,name:textValue(p.Name),country:option(p.Country),
   source_url:url(p['Source URL']),website:url(p.Website),
   review_status:option(p['Review status']),publishable:checkbox(p.Publishable),
   sanctions:option(p.Sanctions),flags:(p.Flags?.multi_select||[]).map(f=>f.name),
  };
  // Fail closed on missing data, unresolved sanctions or any review flag.
  if(company.review_status!=='accepted'||!company.publishable||company.sanctions!=='clear'||company.flags.length)continue;
  if(!/^[A-Z]{2}$/.test(company.country||'')||!company.name?.trim())continue;
  if(!/^https:\/\/(app\.)?notion\.(com|so)\//i.test(company.notion_url||''))continue;
  try{const u=new URL(company.source_url);if(u.protocol!=='https:'||u.username||u.password)continue;}catch{continue;}
  // No street/facility location in this export. Only country polygon.
  exported.set(page.id,company);
 }
 cursor=data.has_more?data.next_cursor:null;
 if(data.has_more&&!cursor)throw Error('Notion has_more without cursor');
 if(++pages>1000)throw Error('Notion pagination exceeded safe limit');
}while(cursor);
if(all===0)throw Error('Notion returned zero records; refusing to replace snapshot');
const companies=[...exported.values()].sort((a,b)=>a.name.localeCompare(b.name)||a.notion_url.localeCompare(b.notion_url));
const snapshot={source:'Notion canonical Companies',generated_at:new Date().toISOString(),total_reviewed:companies.length,precision:'country_only',companies};
await mkdir(dirname(resolve(OUTPUT)),{recursive:true});
await writeFile(OUTPUT,JSON.stringify(snapshot,null,2)+'\n');
console.log('Full Notion scan:',all,'records; eligible:',companies.length,'; wrote',OUTPUT);
