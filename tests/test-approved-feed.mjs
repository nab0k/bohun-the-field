import assert from 'node:assert/strict';
import { validatePublicRecord, loadApprovedIntelligence } from '../src/atlas/approved-feed.js';

const good = {
  qa: { decision: 'APPROVE', public_release: true },
  content_hash: 'a'.repeat(64),
  source_url: 'https://example.org/source',
  headline: 'Source-backed event', published_at: '2026-10-13T06:00:00Z',
  country_iso2: 'AT'
};
assert.equal(validatePublicRecord(good), true);
for (const bad of [
  { ...good, qa: { decision: 'HOLD', public_release: false } },
  { ...good, qa: { decision: 'APPROVE', public_release: false } },
  { ...good, source_url: 'javascript:alert(1)' },
  { ...good, content_hash: 'not-a-hash' },
  { ...good, country_iso2: 'AUT' }
]) assert.equal(validatePublicRecord(bad), false);
const reply = (records, ok = true) => ({
  ok, headers: { get: () => 'application/json' }, json: async () => ({ records })
});
assert.deepEqual(await loadApprovedIntelligence({ fetchImpl: async () => reply([good]) }), [good]);
assert.deepEqual(await loadApprovedIntelligence({ fetchImpl: async () => reply([good, { ...good, qa: { decision: 'HOLD' } }]) }), []);
assert.deepEqual(await loadApprovedIntelligence({ fetchImpl: async () => reply([good], false) }), []);
assert.deepEqual(await loadApprovedIntelligence({ fetchImpl: async () => { throw Error('offline'); } }), []);
console.log('approved intelligence feed: fail-closed checks passed');

import { createAtlasState, selectApproved } from '../src/atlas/m1-state.js';
const state=createAtlasState().get();
assert.deepEqual(selectApproved([good],state),[good]);
for (const malformed of [
 {...good,content_hash:'invalid'},
 {...good,source_url:'https://user:password@example.org/original'},
 {...good,headline:''},
 {...good,published_at:'unknown'},
 {...good,country_iso2:'AUT'},
 {...good,qa:{decision:'HOLD',public_release:true}}
]) assert.deepEqual(selectApproved([malformed],state),[]);
