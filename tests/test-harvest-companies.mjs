import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {parseCSV,processCandidates,harvest} from '../scripts/harvest-companies.mjs';
assert.deepEqual(parseCSV('name,id\n"ABC, Inc.",1\n'),[{name:'ABC, Inc.',id:'1'}]);
assert.throws(()=>parseCSV('name,id\n"oops,1\n'));
const sample=[{name:'Acme Defence',id:'1',country:'GB',website:'https://acme.example'},{name:'Acme Defence',id:'1',country:'GB'},{name:'Other Group',id:'2',country:'GB'},{name:'Broken',id:'3',country:'EUR'}];
const r=processCandidates(sample,{sourceId:'test2026',sourceUrl:'https://example.org/list',sourceHash:'hash'});
assert.equal(r.metrics.accepted,2);assert.equal(r.metrics.source_duplicate,1);assert.equal(r.metrics.holds,1);assert(r.accepted.every(c=>!c.publication_approved&&c.evidence_layer==='D_LEAD_ONLY'));
const dir=await mkdtemp(join(tmpdir(),'bohun-harvest-'));
try{const input=join(dir,'source.csv'),out=join(dir,'out');await writeFile(input,'name,id,country\nAcme,1,GB\nAcme,1,GB\nOther,2,CZ\n');
 const a=await harvest({input,sourceId:'catalog2026',sourceUrl:'https://example.org/catalog',outDir:out});
 const before=await readFile(join(out,'candidates.jsonl'),'utf8');
 const b=await harvest({input,sourceId:'catalog2026',sourceUrl:'https://example.org/catalog',outDir:out});
 const after=await readFile(join(out,'candidates.jsonl'),'utf8');
 assert.deepEqual(a,b);assert.equal(before,after);assert.equal(a.accepted,2);assert.equal(a.public_release_count,0);
}finally{await rm(dir,{recursive:true,force:true})}
console.log('Harvest tests PASS');
