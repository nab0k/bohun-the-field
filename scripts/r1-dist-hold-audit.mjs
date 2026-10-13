// Scan the ACTUAL release dist, including minified JS chunks, not just JSON files.
import {readdirSync,readFileSync,statSync,writeFileSync,mkdirSync} from 'node:fs';
import {join,relative} from 'node:path';
const root='dist',files=[];
const walk=dir=>{for(const name of readdirSync(dir)){const p=join(dir,name);if(statSync(p).isDirectory())walk(p);else files.push(p)}};walk(root);
const forbidden=[
  [/pilot-internal-feed(?:\\.json)?/i,'pilot internal payload reference'],
  [/CORDIS:101289409/i,'SMART HOLD event identity'],
  [/INTERNAL_CANDIDATE/i,'research-only candidate'],
  [/QA[: ]+HOLD/i,'QA HOLD dossier'],
  [/INTERNAL QA HOLD/i,'internal review data'],
  [/vertical-slice-v1-internal/i,'internal fixture reference'],
  [/__livePilot01/i,'dev-only pilot instrumentation']
];
const hits=[];
for(const file of files){if(!/\\.(?:js|json|html|css|txt|map)$/i.test(file))continue;
 const body=readFileSync(file,'utf8');
 for(const [re,reason] of forbidden){const m=re.exec(body);if(m)hits.push({file:relative(root,file),reason,offset:m.index})}
}
const report={checked_files:files.length,scanned_types:'js/json/html/css/txt/map',hits,pass:hits.length===0};
mkdirSync('output/r1-review',{recursive:true});
writeFileSync('output/r1-review/dist-hold-audit.json',JSON.stringify(report,null,2));
console.log(JSON.stringify(report));if(hits.length)process.exit(1);
