import assert from 'node:assert/strict';
import {createAtlasState,selectApproved} from '../src/atlas/m1-state.js';
const store=createAtlasState();let notify=0;const stop=store.subscribe(()=>notify++);
store.selectCountry('AT');assert.equal(store.get().country,'AT');
store.selectCompany('at-041');assert.equal(store.get().company,'at-041');
store.selectEvent('at-event-1');assert.equal(store.get().event,'at-event-1');
store.setFilters({types:['contract','contract']});assert.deepEqual(store.get().filters.types,['contract']);
store.setPeriod('2026-01-01','2026-12-31');assert.equal(store.get().period.from,'2026-01-01');
assert.throws(()=>store.setPeriod('2027-01-01','2026-01-01'));
assert.throws(()=>store.selectCountry('AUT'));
store.selectCountry('AT');assert.equal(store.get().event,null);
const record={qa:{decision:'APPROVE',public_release:true},country_iso2:'AT',company_id:'at-041',
 event_id:'at-event-1',published_at:'2026-05-05',event_type:'contract',sector:'electronics',
 source_url:'https://example.org/official',content_hash:'sha256:abcd',headline:'Test',takeaway:'Test'};
assert.equal(selectApproved([record],store.get()).length,0);
store.selectCompany('at-041');store.selectEvent('at-event-1');
assert.equal(selectApproved([record],store.get()).length,1);
assert.equal(selectApproved([{...record,qa:{decision:'HOLD',public_release:false}}],store.get()).length,0);
assert.equal(selectApproved([{...record,source_url:''}],store.get()).length,0);
assert.equal(selectApproved([{...record,content_hash:''}],store.get()).length,0);
stop();assert.ok(notify>1);
console.log('M1 state and QA-gated panels: PASS (12 contract checks; fixture not published)');
