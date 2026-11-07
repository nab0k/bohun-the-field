from dataclasses import replace
import unittest

from collectors.editor.stages import check_stage_assertions


class StageRuleTests(unittest.TestCase):
    def setUp(self):
        self.evidence = {('S1', 'span1'): 'Contract signature observed'}
        self.events = ('EV1',)
        self.item = {'stage': 'contracted', 'source_id': 'S1',
                     'span_id': 'span1', 'event_id': 'EV1',
                     'event_date': '2026-11-01'}
        self.payload = {'as_of': '2026-11-02', 'stage_assertions': [self.item]}

    def codes(self, payload=None, evidence=None, events=None):
        issues = check_stage_assertions(
            self.payload if payload is None else payload,
            self.evidence if evidence is None else evidence,
            self.events if events is None else events)
        return {i.code for i in issues}

    def test_absent_stage_is_allowed_for_other_content(self):
        self.assertEqual(self.codes({'headline': 'No stage claim'}), set())

    def test_properly_attributed_stage_has_no_structural_issue(self):
        self.assertEqual(self.codes(), set())

    def test_unknown_stage_is_blocked(self):
        self.assertIn('invalid_stage_value', self.codes({'stage_assertions': [{**self.item, 'stage': 'awarded'}]}))

    def test_no_stage_list_is_blocked(self):
        self.assertIn('invalid_stage_collection', self.codes({'stage_assertions': []}))

    def test_wrong_collection_type_is_blocked(self):
        self.assertIn('invalid_stage_collection', self.codes({'stage_assertions': {}}))

    def test_missing_span_is_blocked(self):
        self.assertIn('stage_without_source_span', self.codes(evidence={}))

    def test_invalid_event_id_is_blocked(self):
        self.assertIn('stage_event_mismatch', self.codes(events=('OTHER',)))

    def test_future_completed_stage_is_review(self):
        self.assertIn('stage_claim_postdates_snapshot', self.codes({'as_of':'2026-11-01',
            'stage_assertions': [{**self.item, 'event_date':'2026-11-10'}]}))

    def test_impossible_date_is_blocked(self):
        self.assertIn('invalid_stage_event_date', self.codes({'stage_assertions': [{**self.item, 'event_date':'2026-02-30'}]}))

    def test_invalid_as_of_is_blocked(self):
        self.assertIn('invalid_stage_as_of', self.codes({'as_of': 'tomorrow', 'stage_assertions':[self.item]}))

    def test_naive_timestamp_rejected(self):
        self.assertIn('invalid_stage_event_date', self.codes({'stage_assertions': [{**self.item, 'event_date':'2026-11-01T02:00:00'}]}))

    def test_no_implied_stage_sequence(self):
        item = {**self.item, 'stage':'demonstrated'}
        self.assertEqual(self.codes({'stage_assertions':[item]}), set())

    def test_malformed_entry_is_blocked(self):
        self.assertIn('invalid_stage_assertion', self.codes({'stage_assertions': [None]}))

    def test_source_id_must_be_string(self):
        self.assertIn('stage_without_source_span', self.codes({'stage_assertions': [{**self.item, 'source_id':['S1']}]}))

    def test_stage_cannot_invent_event(self):
        self.assertIn('stage_event_mismatch', self.codes({'stage_assertions': [{**self.item, 'event_id':'EV2'}]}))


if __name__ == '__main__':
    unittest.main()
