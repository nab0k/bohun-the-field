#!/usr/bin/env node
// BOHUNDEFENCE source intake: offline, fail-closed candidate generation.
// Usage: node scripts/harvest-companies.mjs --input source.csv --source-id catalog-2026 --source-url https://example.org/catalog --out-dir .local/harvest
// Supports CSV or JSON arrays of source records. NEVER publishes, alters Notion, or calls external services.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve,join,extname} from 'node:path';
import {createHash} from 'node:crypto';
import {pathToFileURL} from 'node:url';

export function parseCSV(str){
 const rows=[];let row=[],field='',quoted=false;
 for(let i=0;i<str.length;i++){const c=str[i];
  if(quoted){if(c==='"'&&str[i+1]==='"'){field+='"';i++;}else if(c==='"')quoted=false;else field+=c;}
  else if(c==='"'){if(field)throw Error('Unexpected quote');quoted=true;}
  else if(c===','){row.push(field);field='';}
  else if(c==='\n'||c==='\r'){if(c==='\r'&&str[i+1]==='\n')i++;row.push(field);if(row.some(v=>v!==''))rows.push(row);row=[];field='';}
  else field+=c;
 }
 if(quoted)throw Error('Unclosed CSV quote');
 if(field!==''||row.length){row.push(field);rows.push(row);}
 const headers=rows.shift()||[];if(!headers.length||new Set(headers).size!==headers.length)throw Error('CSV headers missing or duplicated');
 return rows.map((r,i)=>{if(r.length!==headers.length)throw Error('CSV column mismatch at record '+(i+1));return Object.fromEntries(headers.map((h,j)=>[h.trim(),r[j].trim()]));});
}
const norm=x=>String(x||'').normalize('NFKC').toLowerCase().replace(/[\p{P}\p{S}]+/gu,' ').replace(/\s+/g,' ').trim();
const https=x=>{if(!x)return null;try{const u=new URL(x);return (u.protocol==='https:'&&!u.username&&!u.password)?u.href:null;}catch{return null;}};
export function processCandidates(input,{sourceId,sourceUrl,sourceHash}){
 if(!Array.isArray(input))throw Error('Input must be an array');
 if(!sourceId||!/^[a-zA-Z0-9_.:-]{3,100}$/.test(sourceId))throw Error('Invalid source id');
 if(!https(sourceUrl))throw Error('Source URL must be HTTPS');
 const accepted=[],holds=[],seen=new Set(),metrics={input_rows:input.length,accepted:0,source_duplicate:0,holds:0,not_verified:0};
 for(let i=0;i<input.length;i++){
  const r=input[i],name=String(r.name||r.Name||r.legal_name||'').trim();
  const sourceRecordId=String(r.source_record_id||r.id||'').trim();
  const country=String(r.country_iso2||r.country||'').trim().toUpperCase();
  const registration=String(r.registration_id||'').trim();
  const jurisdiction=String(r.registration_jurisdiction||'').trim().toUpperCase();
  const website=https(r.website||r.Website),recordUrl=https(r.record_url||r.url||r.source_url)||sourceUrl;
  const rowHash=createHash('sha256').update(JSON.stringify(r)).digest('hex');
  if(!name||name.length>250||(!sourceRecordId&&!recordUrl)){holds.push({row:i+1,reason:'INSUFFICIENT_IDENTITY',row_sha256:rowHash});continue;}
  if(country&&!/^[A-Z]{2}$/.test(country)){holds.push({row:i+1,reason:'INVALID_COUNTRY',row_sha256:rowHash});continue;}
  if((registration&&!jurisdiction)||(jurisdiction&&!registration)){holds.push({row:i+1,reason:'INCOMPLETE_LEGAL_ID',row_sha256:rowHash});continue;}
  const originKey=sourceRecordId||rowHash;
  const sourceKey=sourceId+':'+originKey;
  if(seen.has(sourceKey)){metrics.source_duplicate++;continue;}
  seen.add(sourceKey);
  const key=registration?'legal:'+jurisdiction+':'+norm(registration):'lead:'+sourceKey;
  accepted.push({candidate_id:createHash('sha256').update(key).digest('hex'),source_id:sourceId,source_record_id:sourceRecordId||null,source_url:recordUrl,source_file_sha256:sourceHash,source_row_sha256:rowHash,display_name:name,legal_name:r.legal_name||null,country_iso2:country||null,registration_id:registration||null,registration_jurisdiction:jurisdiction||null,website,identity_status:registration?'ID_CANDIDATE':'UNVERIFIED',evidence_layer:'D_LEAD_ONLY',qa_status:'PENDING',publication_approved:false});
 }
 metrics.accepted=accepted.length;metrics.holds=holds.length;metrics.not_verified=accepted.filter(x=>x.identity_status==='UNVERIFIED').length;
 return {accepted,holds,metrics};
}
export async function harvest({input,sourceId,sourceUrl,outDir}){
 const bytes=await readFile(input);const hash=createHash('sha256').update(bytes).digest('hex');
 const raw=bytes.toString('utf8').replace(/^\\uFEFF/,'');
 const records=extname(input).toLowerCase()==='.csv'?parseCSV(raw):JSON.parse(raw);
 const {accepted,holds,metrics}=processCandidates(records,{sourceId,sourceUrl,sourceHash:hash});
 await mkdir(outDir,{recursive:true});
 await writeFile(join(outDir,'candidates.jsonl'),accepted.map(x=>JSON.stringify(x)).join('\\n')+(accepted.length?'\\n':''));
 await writeFile(join(outDir,'holds.jsonl'),holds.map(x=>JSON.stringify(x)).join('\\n')+(holds.length?'\\n':''));
 const report={source_id:sourceId,source_url:sourceUrl,source_sha256:hash,...metrics,public_release_count:0};
 await writeFile(join(outDir,'report.json'),JSON.stringify(report,null,2)+'\\n');
 return report;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(resolve(process.argv[1])).href){
 const args=Object.fromEntries(process.argv.slice(2).filter((_,i,a)=>i%2===0).map((k,i)=>[k,aDummy(process.argv.slice(2),i)]));
 function aDummy(a,i){return a[i*2+1];}
 const input=args['--input'],sourceId=args['--source-id'],sourceUrl=args['--source-url'],outDir=args['--out-dir'];
 if(!input||!outDir)throw Error('Required: --input CSV/JSON --source-id ID --source-url HTTPS --out-dir DIRECTORY');
 console.log(JSON.stringify(await harvest({input,sourceId,sourceUrl,outDir}),null,2));
}
