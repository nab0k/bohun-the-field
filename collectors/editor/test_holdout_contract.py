"""Synthetic-only, offline holdout-contract regression tests."""
from copy import deepcopy
from hashlib import sha256
import unittest

from collectors.editor.holdout_contract import audit_gold_candidate


def good():
    text = 'Example publisher announces exactly 40 units.'
    claim = 'The publisher announced 40 units.'
    return {
        'schema': 'bohun-c3-10-private-gold-candidate-v1',
        'source_class': 'P2 only; 0 independent P1 attestations',
        'publication_authorized': False, 'provider_calls': 0,
        'selected_material_ids': [7],
        'spans': [{
            'span_id': 'span-1', 'material_id': 'syn-7', 'text': text,
            'start_char_0': 4, 'end_char_exclusive': 4+len(text),
            'text_sha256': sha256(text.encode()).hexdigest(),
            'url': 'https://example.org/report', 'source_class': 'P2',
            'independent_retrieval_attested': False,
        }],
        'cases': [{
            'case_id': 'case-1', 'material_id': 'syn-7',
            'claim_text': claim, 'claim_sha256': sha256(claim.encode()).hexdigest(),
            'expected_source_entailment_label': 'supported',
            'quoted_span_ids': ['span-1'], 'origin': 'source_faithful',
            'independent_second_annotation_complete': False,
        }],
    }


class HoldoutContractTests(unittest.TestCase):
    def test_good_is_provisional_only(self):
        data = good()
        r = audit_gold_candidate(data)
        self.assertEqual(r['counts']['supported'], 1)
        self.assertEqual(r['material_count'], 1)
        self.assertFalse(r['publication_authorized'])
        self.assertFalse(r['real_world_truth_attested'])
        self.assertNotIn(data['cases'][0]['claim_text'], repr(r))
        self.assertNotIn(data['spans'][0]['text'], repr(r))

    def test_changed_quote_fails_hash(self):
        data = good(); data['spans'][0]['text'] += ' spoofed'
        with self.assertRaisesRegex(ValueError, 'span_offsets_inconsistent|span_integrity_mismatch'):
            audit_gold_candidate(data)

    def test_wrong_offset_fails(self):
        data = good(); data['spans'][0]['end_char_exclusive'] += 1
        with self.assertRaisesRegex(ValueError, 'span_offsets_inconsistent'):
            audit_gold_candidate(data)

    def test_forged_source_attestation_fails(self):
        data = good(); data['spans'][0]['independent_retrieval_attested'] = True
        with self.assertRaisesRegex(ValueError, 'unverified_source_trust_escalation'):
            audit_gold_candidate(data)

    def test_second_annotation_fake_fails(self):
        data = good(); data['cases'][0]['independent_second_annotation_complete'] = True
        with self.assertRaisesRegex(ValueError, 'unverified_second_annotation'):
            audit_gold_candidate(data)

    def test_duplicate_case_id_fails(self):
        data = good(); data['cases'].append(deepcopy(data['cases'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate_or_invalid_case_id'):
            audit_gold_candidate(data)

    def test_duplicate_span_id_fails(self):
        data = good(); data['spans'].append(deepcopy(data['spans'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate_or_invalid_span_id'):
            audit_gold_candidate(data)

    def test_forged_claim_hash_fails(self):
        data = good(); data['cases'][0]['claim_text'] += ' extra'
        with self.assertRaisesRegex(ValueError, 'claim_integrity_mismatch'):
            audit_gold_candidate(data)

    def test_unsupported_label_fails(self):
        data = good(); data['cases'][0]['expected_source_entailment_label']='PASS'
        with self.assertRaisesRegex(ValueError, 'invalid_entailment_label'):
            audit_gold_candidate(data)

    def test_missing_span_ref_fails(self):
        data = good(); data['cases'][0]['quoted_span_ids']=['fabricated']
        with self.assertRaisesRegex(ValueError, 'case_span_cross_material_or_missing'):
            audit_gold_candidate(data)

    def test_cross_material_ref_fails(self):
        data = good(); data['spans'][0]['material_id']='syn-9'
        with self.assertRaisesRegex(ValueError, 'case_span_cross_material_or_missing'):
            audit_gold_candidate(data)

    def test_permission_escalation_fails(self):
        data = good(); data['publication_authorized']=True
        with self.assertRaisesRegex(ValueError, 'holdout_has_unauthorized_activity'):
            audit_gold_candidate(data)

    def test_duplicate_selected_id_fails(self):
        data = good(); data['selected_material_ids']=[7,7]
        with self.assertRaisesRegex(ValueError, 'selected_material_ids_mismatch'):
            audit_gold_candidate(data)

    def test_boolean_start_not_accepted(self):
        data = good(); data['spans'][0]['start_char_0']=True
        with self.assertRaisesRegex(ValueError, 'span_offsets_inconsistent'):
            audit_gold_candidate(data)

    def test_model_call_not_allowed(self):
        data = good(); data['provider_calls']=1
        with self.assertRaisesRegex(ValueError, 'holdout_has_unauthorized_activity'):
            audit_gold_candidate(data)


if __name__ == '__main__':
    unittest.main()
