import unittest
from intelligence.gemini_evidence_validator import validate_candidate, CandidateValidationError
E='evidence:test:official:1'
SPAN='<ecMaxContribution>992606.6</ecMaxContribution>'
PACKET={'evidence':[{'evidence_id':E,'exact_excerpt':SPAN}]}
def candidate():
 return {'qa_status':'HOLD','candidate_claims':[{'claim_id':'claim:test:1','evidence_ids':[E],'exact_spans':[SPAN]}],'candidate_event':{'claim_ids':['claim:test:1']}}
class OfflineEvidenceGate(unittest.TestCase):
 def test_valid_stored_span_remains_hold(self):
  self.assertTrue(validate_candidate(PACKET,candidate())['pass'])
 def test_unknown_evidence_rejected(self):
  c=candidate();c['candidate_claims'][0]['evidence_ids']=['unknown']
  with self.assertRaises(CandidateValidationError):validate_candidate(PACKET,c)
 def test_fabricated_span_rejected(self):
  c=candidate();c['candidate_claims'][0]['exact_spans']=['not in official evidence']
  with self.assertRaises(CandidateValidationError):validate_candidate(PACKET,c)
 def test_model_may_not_approve(self):
  c=candidate();c['qa_status']='APPROVE'
  with self.assertRaises(CandidateValidationError):validate_candidate(PACKET,c)
 def test_broken_event_claim_ref_rejected(self):
  c=candidate();c['candidate_event']['claim_ids']=['unknown claim']
  with self.assertRaises(CandidateValidationError):validate_candidate(PACKET,c)
if __name__=='__main__':unittest.main()
