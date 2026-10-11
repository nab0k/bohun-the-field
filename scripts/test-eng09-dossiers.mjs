import fs from 'node:fs';import assert from 'node:assert/strict';
const p=JSON.parse(fs.readFileSync('data/eng09/first-dossiers-qa-only.json','utf8'));
assert.equal(p.records.length,2);
assert.deepEqual(p.records.map(x=>x.country).sort(),['AT','DE']);
for(const rec of p.records){
 assert.equal(rec.public_release,false);
 assert.ok(rec.notion_page_id && rec.legal_name && rec.hq_public_city);
 assert.ok(rec.proof.length>=2);
 for(const proof of rec.proof)assert.ok(proof.url.startsWith('https://')&&proof.statement&&proof.field);
 assert.ok(rec.unknown.length);
}
assert.equal(p.records.find(x=>x.country==='DE').registry_id,'Freiburg HRA 581075');
assert.equal(p.records.find(x=>x.country==='AT').atlas_research_id,'at-041');
assert.deepEqual(p.event_joins,[]);
console.log('ENG09: two real Notion company profiles / two countries / source audit PASS; publication HOLD');
