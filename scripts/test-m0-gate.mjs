import assert from 'node:assert/strict';
import { qaGate } from '../src/qa/m0-gate.mjs';
const realFixture={document_id:'ted-official-fixture',document_hash:'sha256:internal-evidence-identity',
 source_url:'https://ted.europa.eu/',exact_excerpt:'Official primary notice text',
 usage_rights_verified:true,entity_id:'test-public-buyer',event_identity:'notice-unique-id',
 claims:[{verdict:'SUPPORTS',quote:'Official primary notice text'}],
 model_check_ok:true,independent_check_ok:true,risk_tier:'R0',auto_class_calibrated:true,kill_switch:false};
assert.equal(qaGate(realFixture).decision,'APPROVE'); // Policy unit test, NOT a real TED QA approval.
assert.equal(qaGate({...realFixture,duplicate_of:'notice-unique-id'}).decision,'REJECT');
assert.equal(qaGate({...realFixture,exact_excerpt:''}).decision,'HOLD');
assert.equal(qaGate({...realFixture,independent_check_ok:false}).decision,'HOLD');
assert.equal(qaGate({...realFixture,risk_tier:'R2'}).decision,'HOLD');
assert.equal(qaGate({...realFixture,kill_switch:true}).decision,'HOLD');
assert.equal(qaGate({...realFixture,material_claim_refuted:true}).decision,'REJECT');
console.log('M0 QA v1.1 policy fixtures PASS (7; not real E2E validation)');
