import copy
import json
import unittest
from uk_hold_pilot import prepare_releases, summary, PilotError
from uk_hold_writer import classify, render_sql_plan

def row(rid='release-1',ocid='ocds-a',title='Public synthetic UK tender',tag=('tender',)):
    return {'id':rid,'ocid':ocid,'date':'2026-10-15T09:05:00Z',
            'tender':{'title':title},'tag':list(tag)}

def prepare(*rows,complete=True,max_tenders=10):
    return prepare_releases([{'releases':list(rows),'links':{}}],
                            verified_complete=complete,max_tenders=max_tenders)

class UKPilotTests(unittest.TestCase):
    def test_one_real_shape(self):
        x=prepare(row())
        self.assertEqual(x['pilot_candidates'],1)
        c=x['candidates'][0]
        self.assertEqual(c.external_id,'ocds-a::release-1')
        self.assertEqual(c.item_type,'tender')
        self.assertEqual(c.url,None)
        self.assertEqual(c.raw_data['bohun']['qa'],'HOLD')
        self.assertEqual(c.raw_data['bohun']['public_release'],False)
        self.assertEqual(c.raw_data['bohun']['current_procurement_status'],'unknown')
    def test_award_excluded(self):
        x=prepare(row(rid='a1',tag=('award',)),row(rid='t1'))
        self.assertEqual(x['pilot_candidates'],1)
        self.assertEqual(x['all_candidate_types']['contract_award_notice'],1)
    def test_exact_duplicate_deduped(self):
        r=row()
        x=prepare(r,r)
        self.assertEqual(x['observations'],2)
        self.assertEqual(x['pilot_candidates'],1)
    def test_conflict_rejected(self):
        a=row(); b=copy.deepcopy(a); b['tender']['title']='changed'
        with self.assertRaisesRegex(PilotError,'conflicting_versions'):
            prepare(a,b)
    def test_overlong_combined_id_rejected(self):
        with self.assertRaisesRegex(PilotError,'identity_invalid'):
            prepare(row(ocid='Q'*500,rid='P'*20))
    def test_bad_date_does_not_guess(self):
        r=row();r['date']='October 2026'
        self.assertIsNone(prepare(r)['candidates'][0].published_at)
    def test_missing_timezone_does_not_guess(self):
        r=row();r['date']='2026-10-15T09:00:00'
        self.assertIsNone(prepare(r)['candidates'][0].published_at)
    def test_not_verified_complete_rejected(self):
        with self.assertRaisesRegex(PilotError,'not_verified_complete'):
            prepare(row(),complete=False)
    def test_next_link_present_rejected(self):
        with self.assertRaisesRegex(PilotError,'window_incomplete'):
            prepare_releases([{'releases':[row()], 'links':{'next':'?cursor=a'}}],verified_complete=True)
    def test_count_limit(self):
        with self.assertRaisesRegex(PilotError,'too_large'):
            prepare(*[row(rid=str(i)) for i in range(101)])
    def test_pilot_max_ten(self):
        x=prepare(*[row(rid=str(i)) for i in range(12)])
        self.assertEqual(x['pilot_candidates'],10)
        self.assertEqual(x['eligible_unique_tender_notices'],12)
        self.assertTrue(x['selection_limited'])
    def test_public_summary_private_data(self):
        x=prepare(row(title='HIDDEN TITLE'))
        s=json.dumps(summary(x))
        self.assertNotIn('HIDDEN TITLE',s)
        self.assertNotIn('ocds-a',s)
        self.assertNotIn('source_record_sha256',s)
    def test_planner_insert_repeat_change(self):
        a=prepare(row())['candidates'][0]
        x=classify({},[a])
        self.assertEqual(x[0].effect,'insert_hold')
        old={'uk_fts':{'source_record_sha256':a.record_sha256}}
        self.assertEqual(classify({a.external_id:old},[a])[0].effect,'refresh_seen_only')
        b=prepare(row(title='changed'))['candidates'][0]
        self.assertEqual(classify({a.external_id:old},[b])[0].effect,'new_metadata_version_review')
    def test_unknown_existing_provenance_blocked(self):
        c=prepare(row())['candidates'][0]
        with self.assertRaisesRegex(PilotError,'existing_item_without_expected_provenance'):
            classify({c.external_id:{'bohun':{}}},[c])
    def test_no_db_write(self):
        c=prepare(row())['candidates'][0]
        plan=render_sql_plan(classify({},[c]))
        self.assertEqual(plan['writes_executed'],0)
        self.assertFalse(plan['ready_for_import'])
        self.assertEqual(plan['counts'],{'insert_hold':1})
    def test_duplicate_batch_blocked(self):
        c=prepare(row())['candidates'][0]
        with self.assertRaisesRegex(PilotError,'duplicate_batch_identity'):
            classify({},[c,c])

if __name__=='__main__':
    unittest.main()
