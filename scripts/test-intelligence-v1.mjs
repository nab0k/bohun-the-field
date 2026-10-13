import fs from 'node:fs';import crypto from 'node:crypto';import assert from 'node:assert/strict';
const packet=JSON.parse(fs.readFileSync('intelligence/fixtures/vertical-slice-v1-internal.json','utf8'));
assert.equal(packet.review_only,true);
assert.equal(packet.packets.length,3);
const [au,frequentis,ted]=packet.packets;
assert.equal(au.case_id,'AU-F35-01');assert.equal(au.event.event_id,'AU-F35-01');
assert.equal(au.publication.qa_status,'HOLD');assert.equal(au.publication.public_release,false);
assert.equal(au.event.geo_role,'COUNTRY_ONLY');assert.equal(au.event.coordinates,null);
assert.equal(au.source.published_at,null);assert.equal(au.source.rights,'UNKNOWN_NEEDS_QA');
const e=au.evidence[0],c=au.claims[0];
const input=[e.source_id,e.content_sha256,e.locator,e.exact_excerpt].join('\x1f');
const expected='EV-'+crypto.createHash('sha256').update(input).digest('hex').slice(0,20);
assert.equal(expected,e.evidence_id);
assert.deepEqual(c.evidence_ids,[e.evidence_id]);assert.equal(c.independent_validation,'NOT_ENOUGH_INFO');
assert.equal(frequentis.state,'BLOCKED_ORIGINAL_PDF_SPANS');
assert.equal(ted.state,'BLOCKED_PROCUREMENT04');
for(const p of packet.packets){assert.equal(p.publication.public_release,false)}
console.log(JSON.stringify({cases:3,original_evidence_packets:1,blocked_packets:2,
  source_hash_pinned:true,evidence_id_exact:true,approved:0,publications:0,model_calls:0,qa:'HOLD'}));
