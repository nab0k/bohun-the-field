import unittest
import importlib.util
import pathlib
import xml.etree.ElementTree as ET
SPEC=importlib.util.spec_from_file_location('cordis_qa',pathlib.Path(__file__).with_name('cordis-gemini-evidence-pilot.py'))
MOD=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
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
class FinancialEvidenceSemantics(unittest.TestCase):
 def test_namespace_xpath_resolves_exact_amount(self):
  root=ET.fromstring('<project xmlns="http://cordis.europa.eu"><ecMaxContribution>992606.6</ecMaxContribution></project>')
  self.assertEqual(MOD.resolve_financial_element(root,'/project/ecMaxContribution'),'992606.6')
 def test_wrong_namespace_fails_closed(self):
  root=ET.fromstring('<project><ecMaxContribution>992606.6</ecMaxContribution></project>')
  with self.assertRaises(ValueError):MOD.resolve_financial_element(root,'/project/ecMaxContribution')
 def test_absent_or_ambiguous_amount_fails_closed(self):
  root=ET.fromstring('<project xmlns="http://cordis.europa.eu"><ecMaxContribution>1</ecMaxContribution><ecMaxContribution>2</ecMaxContribution></project>')
  with self.assertRaises(ValueError):MOD.resolve_financial_element(root,'/project/ecMaxContribution')
 def test_exact_max_contribution_accepted(self):
  self.assertTrue(MOD.validate_financial_claim({'predicate':'reported_ec_max_contribution','object':{'value':'992606.6','datatype':'decimal','unit':'EUR'}},'992606.6'))
 def test_payment_claim_rejected(self):
  with self.assertRaises(ValueError):MOD.validate_financial_claim({'predicate':'funds_disbursed','object':{'value':'992606.6','datatype':'decimal','unit':'EUR'}},'992606.6')
 def test_contract_claim_rejected(self):
  with self.assertRaises(ValueError):MOD.validate_financial_claim({'predicate':'defence_contract_award','object':{'value':'992606.6','datatype':'decimal','unit':'EUR'}},'992606.6')
 def test_total_budget_claim_rejected(self):
  with self.assertRaises(ValueError):MOD.validate_financial_claim({'predicate':'project_total_budget','object':{'value':'992606.6','datatype':'decimal','unit':'EUR'}},'992606.6')
 def test_mismatched_amount_or_unit_rejected(self):
  for value,unit in [('992606.7','EUR'),('992606.6','USD')]:
   with self.subTest(value=value,unit=unit):
    with self.assertRaises(ValueError):MOD.validate_financial_claim({'predicate':'reported_ec_max_contribution','object':{'value':value,'datatype':'decimal','unit':unit}},'992606.6')
if __name__=='__main__':unittest.main()
