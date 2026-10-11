// M0 pure fail-closed QA gate, v1.1 candidate-level proof obligations.
// Not a calibrated production auto-release model.
export function qaGate(candidate) {
 const reasons=[];
 if (!candidate || !candidate.document_id || !candidate.document_hash || !candidate.source_url || !candidate.exact_excerpt) reasons.push('MISSING_SOURCE_EVIDENCE');
 if (!candidate?.usage_rights_verified) reasons.push('RIGHTS_NOT_VERIFIED');
 if (!candidate?.entity_id || !candidate?.event_identity) reasons.push('IDENTITY_NOT_VERIFIED');
 if (candidate?.sensitive_geography || candidate?.risk_tier==='R3') reasons.push('SENSITIVE_OR_R3');
 if (candidate?.duplicate_of) return {decision:'REJECT',reason_codes:['DUPLICATE_CANDIDATE'],qa_rule:'v1.1',public_release:false};
 if (candidate?.material_claim_refuted) return {decision:'REJECT',reason_codes:['REFUTED_CANDIDATE'],qa_rule:'v1.1',public_release:false};
 for(const claim of candidate?.claims||[]) {
   if(!['SUPPORTS','REFUTES','NOT_ENOUGH_INFO'].includes(claim.verdict)) reasons.push('INVALID_CLAIM_VERDICT');
   if(claim.verdict!=='SUPPORTS') reasons.push('CLAIM_NOT_SUPPORTED');
   if(!claim.quote || claim.quote!==candidate.exact_excerpt && !candidate.exact_excerpt.includes(claim.quote)) reasons.push('QUOTE_NOT_LITERAL');
 }
 if(!candidate?.claims?.length) reasons.push('NO_CLAIMS');
 if(!candidate?.model_check_ok || !candidate?.independent_check_ok) reasons.push('MODEL_GATE_NOT_VERIFIED');
 if(candidate?.risk_tier!=='R0' || !candidate?.auto_class_calibrated) reasons.push('RISK_CLASS_NOT_CALIBRATED');
 if(candidate?.kill_switch!==false) reasons.push('KILL_SWITCH_ACTIVE');
 return {decision:reasons.length?'HOLD':'APPROVE',reason_codes:[...new Set(reasons)],qa_rule:'v1.1',public_release:reasons.length===0};
}
