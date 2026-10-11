import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import fs from 'node:fs';
const proc=spawnSync('python3',['scripts/m1-one-event-private.py'],{
 env:{PATH:process.env.PATH},encoding:'utf8'});
assert.equal(proc.status,1);
const audit=JSON.parse(fs.readFileSync('output/m1/one-record/audit.json','utf8'));
assert.equal(audit.public_release,false);
assert.equal(audit.decision,'HOLD');
assert.equal(audit.provider_calls.gemini,0);
assert.equal(audit.provider_calls.cloudflare_glm,0);
assert.ok(audit.reasons.includes('PROVIDER_FREE_QUOTA_NOT_VERIFIED'));
console.log('M1 AI independent-validator fail-closed: PASS, zero external requests');
