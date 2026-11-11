"""Offline regression tests against exact GitHub main normalize.py blob."""
import copy
import json
import unittest

from normalize import NormalizationError, prepare
from review_gate import ReviewGateStop, plan, validate_manifest


def uk_release(ocid='ocds-demo-proc', rid='r1', title='Synthetic UK tender', tag='tender'):
    return {'ocid': ocid, 'id': rid, 'tender': {'title': title}, 'tag': [tag]}


def uk(*rows):
    return prepare('uk_fts', {'releases': list(rows)}, scope='one_hour')


def ca(scope='ca_new', ref='SYN-42', title='Synthetic CA tender'):
    return prepare('canadabuys', [{'referenceNumber-numeroReference': ref,
                                  'title-titre-eng': title}], scope=scope)


def eu(code='1'):
    return prepare('eu_funding', {'results': [
        {'reference': 'EU-42', 'metadata': {'title': ['Synthetic EDF call'], 'type': [code]}}
    ], 'totalResults': 1}, scope='edf_grants')


class MainContractCompatibility(unittest.TestCase):
    def test_main_contract_is_required(self):
        batch = ca()
        self.assertEqual(batch['contract_version'], 'bohun-source-review-v1')
        self.assertEqual(validate_manifest(batch)[0], 'canadabuys')

    def test_r2_old_contract_is_blocked(self):
        batch = ca()
        batch['contract_version'] = 'bohun-source-candidate-review-v1'
        with self.assertRaisesRegex(ReviewGateStop, 'contract_mismatch'):
            plan([batch])

    def test_uk_and_ca_and_eu_accepted_for_offline_review(self):
        report = plan([uk(uk_release()), ca(), eu()])
        self.assertEqual(report['source_systems'], ['canadabuys', 'eu_funding', 'uk_fts'])
        self.assertEqual(report['input_sightings'], 3)
        self.assertFalse(report['db_write_allowed'])
        self.assertFalse(report['publication_allowed'])
        self.assertEqual(report['qa'], 'HOLD')

    def test_canada_new_open_counts_one_identity_two_sightings(self):
        report = plan([ca('ca_new'), ca('ca_open')])
        self.assertEqual(report['input_sightings'], 2)
        self.assertEqual(report['distinct_source_external_ids'], 1)
        self.assertEqual(report['canada_cross_scope_candidates'][0]['sighting_count'], 2)
        self.assertEqual(report['canada_cross_scope_candidates'][0]['distinct_payload_hashes'], 1)

    def test_canada_changed_metadata_is_review_not_state_change(self):
        report = plan([ca('ca_new'), ca('ca_open', title='Revised synthetic')])
        difference = report['canada_cross_scope_candidates'][0]
        self.assertEqual(difference['distinct_payload_hashes'], 2)
        self.assertEqual(difference['decision'], 'HUMAN_REVIEW_NO_AUTO_STATE_CHANGE')

    def test_uk_multi_release_ocid_is_link_not_merge(self):
        report = plan([uk(uk_release('ocid-1', 'rel-1'), uk_release('ocid-1', 'rel-2', tag='award'))])
        self.assertEqual(report['input_sightings'], 2)
        self.assertEqual(report['distinct_source_external_ids'], 2)
        self.assertEqual(report['uk_multi_release_procedure_candidates'][0]['release_count'], 2)
        self.assertEqual(report['uk_multi_release_procedure_candidates'][0]['decision'], 'SAME_PROCEDURE_CANDIDATE_ONLY')

    def test_uk_different_ocid_not_linked(self):
        report = plan([uk(uk_release('ocid-1', 'rel-1'), uk_release('ocid-2', 'rel-2'))])
        self.assertEqual(report['uk_multi_release_procedure_candidates'], [])

    def test_eu_unknown_code_review_only(self):
        report = plan([eu('NOT_MAPPED')])
        self.assertFalse(report['publication_allowed'])

    def test_eu_incomplete_page_fail_closed_in_upstream_normalizer(self):
        with self.assertRaisesRegex(NormalizationError, 'incomplete'):
            prepare('eu_funding', {'results': [
                {'reference': 'EU-1', 'metadata': {'title': ['Call']}}
            ], 'totalResults': 9}, scope='edf_grants')

    def test_uk_cursor_incomplete_fail_closed_in_upstream_normalizer(self):
        with self.assertRaisesRegex(NormalizationError, 'uk_incomplete_page'):
            prepare('uk_fts', {'releases': [uk_release()], 'links': {'next': '?cursor=next'}}, scope='one_hour')

    def test_wrong_source_kind_rejected(self):
        batch = ca()
        batch['items'][0]['item_type_candidate'] = 'contract_award_notice'
        with self.assertRaisesRegex(ReviewGateStop, 'record_source_or_kind_mismatch'):
            plan([batch])

    def test_elevated_status_rejected(self):
        for field, value in [('public_release', True), ('verification_status', 'APPROVED'),
                             ('capture_status', 'BODY_PRESENT')]:
            with self.subTest(field=field):
                batch = ca()
                batch['items'][0][field] = value
                with self.assertRaisesRegex(ReviewGateStop, 'unsafe_record_state'):
                    plan([batch])

    def test_manifest_public_release_rejected(self):
        batch = ca()
        batch['public_release'] = True
        with self.assertRaisesRegex(ReviewGateStop, 'unsafe_manifest_state'):
            plan([batch])

    def test_missing_hash_rejected(self):
        batch = ca()
        batch['items'][0]['source_record_sha256'] = 'X' * 64
        with self.assertRaisesRegex(ReviewGateStop, 'invalid_record_digest'):
            plan([batch])

    def test_wrong_provenance_rejected(self):
        batch = ca()
        batch['items'][0]['source_metadata']['source_system'] = 'uk_fts'
        with self.assertRaisesRegex(ReviewGateStop, 'record_provenance_mismatch'):
            plan([batch])

    def test_duplicate_record_in_batch_rejected(self):
        batch = ca()
        batch['items'].append(copy.deepcopy(batch['items'][0]))
        with self.assertRaisesRegex(ReviewGateStop, 'duplicate_record_identity'):
            plan([batch])

    def test_duplicate_source_scope_rejected(self):
        batch = ca()
        with self.assertRaisesRegex(ReviewGateStop, 'repeated_source_scope'):
            plan([batch, copy.deepcopy(batch)])

    def test_unrecognized_scope_rejected(self):
        batch = ca()
        batch['collection_scope'] = 'ca_awards'
        with self.assertRaisesRegex(ReviewGateStop, 'scope_unknown'):
            plan([batch])

    def test_empty_snapshot_cannot_indicate_cancellation(self):
        batch = prepare('canadabuys', [], scope='ca_open')
        report = plan([batch])
        self.assertEqual(report['input_sightings'], 0)
        self.assertFalse(report['db_write_allowed'])
        self.assertIn('Absence from snapshot never proves closure or cancellation', report['warnings'])

    def test_report_contains_no_raw_title_or_external_id(self):
        batch = ca(ref='SENSITIVE_FAKE_REF_398', title='Sensitive fake title never shown')
        report = json.dumps(plan([batch]))
        self.assertNotIn('SENSITIVE_FAKE_REF_398', report)
        self.assertNotIn('Sensitive fake title never shown', report)

    def test_receipts_do_not_authorize_live_or_writes(self):
        batch = ca()
        receipt = {'source_system': 'canadabuys', 'collection_scope': 'ca_new', 'complete': True}
        report = plan([batch], receipts=[receipt])
        self.assertFalse(report['metadata_receipts'][0]['independently_verified'])
        self.assertTrue(report['metadata_receipts'][0]['claimed_complete'])
        self.assertFalse(report['db_write_allowed'])

    def test_receipt_other_scope_rejected(self):
        batch = ca()
        with self.assertRaisesRegex(ReviewGateStop, 'receipt_scope_or_completeness_invalid'):
            plan([batch], receipts=[{'source_system': 'canadabuys', 'collection_scope': 'ca_open', 'complete': True}])

    def test_malformed_source_type_fails_closed(self):
        batch = ca()
        batch['source_system'] = ['canadabuys']
        with self.assertRaisesRegex(ReviewGateStop, 'source_unknown'):
            plan([batch])

    def test_malformed_kind_type_fails_closed(self):
        batch = ca()
        batch['items'][0]['item_type_candidate'] = ['procurement_notice']
        with self.assertRaisesRegex(ReviewGateStop, 'record_source_or_kind_mismatch'):
            plan([batch])

    def test_malformed_receipt_scope_fails_closed(self):
        batch = ca()
        with self.assertRaisesRegex(ReviewGateStop, 'receipt_scope_or_completeness_invalid'):
            plan([batch], receipts=[{'source_system': ['canadabuys'],
                                     'collection_scope': 'ca_new', 'complete': True}])

    def test_no_manifests_rejected(self):
        with self.assertRaisesRegex(ReviewGateStop, 'manifest_count_invalid'):
            plan([])

    def test_uk_bad_release_id_rejected(self):
        batch = uk(uk_release())
        batch['items'][0]['external_id'] = 'bad-no-release-part'
        with self.assertRaisesRegex(ReviewGateStop, 'uk_release_identity_required'):
            plan([batch])

    def test_mismatched_source_rejected(self):
        batch = ca()
        batch['items'][0]['source_system'] = 'eu_funding'
        with self.assertRaisesRegex(ReviewGateStop, 'record_source_or_kind_mismatch'):
            plan([batch])


if __name__ == '__main__':
    unittest.main()
