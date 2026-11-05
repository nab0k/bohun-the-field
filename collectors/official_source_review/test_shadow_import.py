import json
import unittest
from normalize import prepare
from shadow_import import build_shadow, ShadowImportStop


def ca(ref='CA1', title='Synthetic Canadian tender'):
    return {'referenceNumber-numeroReference': ref,'title-titre-eng': title}

def uk(ref='R1', title='Synthetic UK tender'):
    return {'ocid':'ocds-demo','id':ref,'tag':['tender'],'tender':{'title':title}}

def eu(ref='EU1', title='Synthetic EDF call'):
    return {'reference':ref,'metadata':{'title':[title],'type':['1']}}

def snap(t, source, data,scope,complete=None):
    obj={'observed_at':t,'manifest':prepare(source,data,scope=scope)}
    if complete is not None:
        obj['receipt']={'source_system':source,'collection_scope':scope,'complete':complete}
    return obj

T1='2026-10-20T09:00:00Z'
T2='2026-10-20T10:00:00+00:00'
T3='2026-10-20T11:00:00Z'

class ShadowTests(unittest.TestCase):
    def test_single_first_version(self):
        s=snap(T1,'canadabuys',[ca()], 'ca_new')
        r=build_shadow([s])
        self.assertEqual((r['distinct_source_external_ids'],r['distinct_observed_payload_hashes']), (1,1))
        self.assertEqual(r['observed_content_change_events'],0)

    def test_cross_scope_same_payload(self):
        s1=snap(T1,'canadabuys',[ca()], 'ca_new')
        s2=snap(T1,'canadabuys',[ca()], 'ca_open')
        r=build_shadow([s1,s2])
        self.assertEqual(r['distinct_source_external_ids'],1)
        self.assertEqual(r['distinct_observed_payload_hashes'],1)
        self.assertEqual(r['cross_scope_identity_candidates'],1)
        self.assertEqual(r['observed_content_change_events'],0)

    def test_changed_next_day_not_auto_state(self):
        ss=[snap(T1,'canadabuys',[ca()], 'ca_new'), snap(T2,'canadabuys',[ca(title='Amended tender')], 'ca_open')]
        r=build_shadow(ss)
        self.assertEqual(r['distinct_observed_payload_hashes'],2)
        self.assertEqual(r['observed_content_change_events'],1)
        self.assertFalse(r['db_write_allowed'])

    def test_conflict_same_time_quarantined(self):
        ss=[snap(T1,'canadabuys',[ca(title='A')], 'ca_new'),snap(T1,'canadabuys',[ca(title='B')], 'ca_open')]
        r=build_shadow(ss)
        self.assertEqual(r['conflicting_same_instant_identities'],1)
        self.assertEqual(r['observed_content_change_events'],0)
        self.assertEqual(r['distinct_observed_payload_hashes'],0)

    def test_conflict_then_later_good_version(self):
        ss=[snap(T1,'canadabuys',[ca(title='A')], 'ca_new'),snap(T1,'canadabuys',[ca(title='B')], 'ca_open'),snap(T2,'canadabuys',[ca(title='B')], 'ca_open')]
        r=build_shadow(ss)
        self.assertEqual(r['conflicting_same_instant_identities'],1)
        self.assertEqual(r['distinct_observed_payload_hashes'],1)

    def test_same_hash_repeated(self):
        r=build_shadow([snap(T1,'canadabuys',[ca()], 'ca_open'),snap(T2,'canadabuys',[ca()], 'ca_open')])
        self.assertEqual(r['distinct_observed_payload_hashes'],1)
        self.assertEqual(r['observed_content_change_events'],0)

    def test_missing_next_time_not_closure(self):
        r=build_shadow([snap(T1,'canadabuys',[ca()], 'ca_open'),snap(T2,'canadabuys',[], 'ca_open')])
        self.assertEqual(r['distinct_source_external_ids'],1)
        self.assertFalse('closed' in r)
        self.assertFalse('cancelled' in r)

    def test_uk_multiple_releases_not_versioned_single_identity(self):
        r=build_shadow([snap(T1,'uk_fts',{'releases':[uk('R1'),uk('R2')]},'one_hour')])
        self.assertEqual(r['distinct_source_external_ids'],2)
        self.assertEqual(r['uk_multi_release_ocid_candidates'],1)
        self.assertEqual(r['observed_content_change_events'],0)

    def test_eu_not_automatic_grant_approval(self):
        r=build_shadow([snap(T1,'eu_funding',{'results':[eu()]}, 'edf_grants')])
        self.assertEqual(r['source_systems'],['eu_funding'])
        self.assertFalse(r['public_release'])
        self.assertEqual(r['qa'],'HOLD')

    def test_output_does_not_contain_title_id(self):
        r=build_shadow([snap(T1,'canadabuys',[ca('SECRET_REF','SECRET_TITLE')],'ca_new')])
        s=json.dumps(r)
        self.assertNotIn('SECRET_REF',s)
        self.assertNotIn('SECRET_TITLE',s)

    def test_require_aware_timestamp(self):
        with self.assertRaisesRegex(ShadowImportStop,'timezone_required'):
            build_shadow([snap('2026-10-20T09:00:00','canadabuys',[ca()], 'ca_new')])

    def test_reject_nonstrict_chronology(self):
        with self.assertRaisesRegex(ShadowImportStop,'events_not_chronological'):
            build_shadow([snap(T2,'canadabuys',[ca()], 'ca_new'), snap(T1,'canadabuys',[ca()], 'ca_new')])

    def test_duplicate_source_scope_time(self):
        with self.assertRaisesRegex(ShadowImportStop,'duplicate_snapshot_run'):
            build_shadow([snap(T1,'canadabuys',[ca()],'ca_new'),snap(T1,'canadabuys',[ca()],'ca_new')])

    def test_bad_receipt_source(self):
        s=snap(T1,'canadabuys',[ca()],'ca_new',True)
        s['receipt']['source_system']='uk_fts'
        with self.assertRaisesRegex(ShadowImportStop,'receipt_not_matching'):
            build_shadow([s])

    def test_complete_is_only_caller_claim(self):
        r=build_shadow([snap(T1,'canadabuys',[ca()],'ca_new',True)])
        self.assertTrue(r['receipt_claims']['claimed_complete'])
        self.assertFalse(r['independently_verified_source_completeness'])

    def test_zero_snapshot_rejected(self):
        with self.assertRaisesRegex(ShadowImportStop,'snapshot_count_invalid'):
            build_shadow([])

    def test_snapshot_limit(self):
        with self.assertRaisesRegex(ShadowImportStop,'snapshot_count_invalid'):
            build_shadow([{}]*25)

    def test_empty_manifest_accepted_as_empty_not_closure(self):
        r=build_shadow([snap(T1,'canadabuys',[],'ca_open')])
        self.assertEqual(r['distinct_source_external_ids'],0)
        self.assertEqual(r['observed_content_change_events'],0)

    def test_version_a_b_a_again(self):
        r=build_shadow([snap(T1,'canadabuys',[ca(title='A')],'ca_open'),snap(T2,'canadabuys',[ca(title='B')],'ca_open'),snap(T3,'canadabuys',[ca(title='A')],'ca_open')])
        self.assertEqual(r['distinct_observed_payload_hashes'],2)
        self.assertEqual(r['observed_content_change_events'],2)

    def test_invalid_manifest_halt(self):
        s=snap(T1,'canadabuys',[ca()],'ca_new')
        s['manifest']['qa']='APPROVE'
        with self.assertRaisesRegex(ShadowImportStop,'review_gate_unsafe'):
            build_shadow([s])

    def test_no_db_network_files_client_imports(self):
        import shadow_import
        self.assertFalse(hasattr(shadow_import,'supabase'))
        self.assertFalse(hasattr(shadow_import,'requests'))

if __name__=='__main__':unittest.main()
