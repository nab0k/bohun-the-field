import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {validateNotionCompany,loadNotionCompanies} from '../src/atlas/notion-companies.js';
const valid={name:'Verified company',country:'AT',source_url:'https://source.example/company',notion_url:'https://app.notion.com/example',review_status:'accepted',publishable:true,sanctions:'clear',flags:[]};
assert.equal(validateNotionCompany(valid),true);
for(const changes of [
 {review_status:'unresolved'},{publishable:false},{sanctions:'pending'},
 {country:null},{source_url:'javascript:alert(1)'},{flags:['LEGAL REVIEW']},
 {notion_url:'https://unknown.example/row'}
])assert.equal(validateNotionCompany({...valid,...changes}),false);
const fetchImpl=async()=>({ok:true,json:async()=>({companies:[valid]})});
assert.deepEqual(await loadNotionCompanies({fetchImpl}),[valid]);
const badFetch=async()=>({ok:true,json:async()=>({companies:[valid,{...valid,sanctions:'hit'}]})});
assert.deepEqual(await loadNotionCompanies({fetchImpl:badFetch}),[]);
try {
 const file=JSON.parse(await readFile('public/atlas/notion-companies.json','utf8'));
 assert.equal(file.precision,'country_only');
 assert.ok(Array.isArray(file.companies));
 assert.ok(file.companies.every(validateNotionCompany),'A non-approved company leaked to Atlas snapshot');
 assert.equal(new Set(file.companies.map(x=>x.notion_url)).size,file.companies.length,'Duplicate Notion identity');
 console.log('Validated actual Notion company snapshot:',file.companies.length);
} catch(e){if(e.code!=='ENOENT')throw e;console.log('Snapshot not yet generated; adapter contract tests only');}

// Offline mass-harvest contract (no external network or mutations).
await import('./test-harvest-companies.mjs');

import {execFileSync} from 'node:child_process';
execFileSync('python3',['-m','unittest','discover','-s','tests','-p','test_global_harvester.py','-v'],{stdio:'inherit'});
