"""R5.1 offline guard tests: never use external services."""
import json
import unittest
from r5_quality_hardened import quality_report

class GateTests(unittest.TestCase):
    def test_good_uk(self):
        row={"ocid":"ocds-a","id":"a","tender":{"title":"Sensitive"},"tag":["tender"]}
        x=quality_report("uk",[row],source_complete=True)
        self.assertEqual(x["unique_candidates"],1)
        self.assertEqual(x["quality_gate"],"NO_ROW_ERRORS_DETECTED")
        self.assertNotIn("Sensitive",json.dumps(x))
        self.assertFalse(x["ready_for_import"])
    def test_uk_combined_id_truncation_rejected(self):
        r={"ocid":"X"*499,"id":"Z"*25,"tender":{"title":"t"}}
        x=quality_report("uk",[r],source_complete=True)
        self.assertEqual(x["rejection_reasons"],{"identity_overlong":1})
        self.assertEqual(x["unique_candidates"],0)
    def test_uk_id_collision_not_possible(self):
        a={"ocid":"K"*509,"id":"A","tender":{"title":"t"}}
        b={"ocid":"K"*509,"id":"B","tender":{"title":"t"}}
        x=quality_report("uk",[a,b],source_complete=True)
        self.assertEqual(x["unique_candidates"],2)
    def test_ca_id_truncation_rejected(self):
        r={"referenceNumber-numeroReference":"R"*513,"title-titre-eng":"t"}
        x=quality_report("ca_new",[r],source_complete=True)
        self.assertEqual(x["rejected_rows"],1)
        self.assertNotIn("R"*100,json.dumps(x))
    def test_eu_id_truncation_rejected(self):
        x=quality_report("eu",[{"reference":"A"*1000,"metadata":{"title":["EU"]}}],source_complete=False)
        self.assertEqual(x["rejection_reasons"],{"identity_overlong":1})
        self.assertTrue(x["sample_only"])
    def test_rejection_reason_safe(self):
        r={"referenceNumber-numeroReference":"X","title-titre-eng":None}
        x=quality_report("ca_open",[r],source_complete=True)
        self.assertEqual(x["rejection_reasons"],{"missing_id_or_title":1})
    def test_distinguishes_duplicate_conflict(self):
        a={"referenceNumber-numeroReference":"key","title-titre-eng":"one"}
        b={**a,"title-titre-eng":"two"}
        x=quality_report("ca_new",[a,a,b],source_complete=True)
        self.assertEqual((x["unique_candidates"],x["duplicate_observations"],x["conflicting_versions"]),(1,1,1))
        self.assertEqual(x["quality_gate"],"REVIEW_REQUIRED")
    def test_eu_incomplete_blocks_ready(self):
        x=quality_report("eu",[{"reference":"E1","metadata":{"title":["Test"],"type":["1"]}}],source_complete=False,reported_total=415)
        self.assertFalse(x["ready_for_import"])
        self.assertEqual(x["reported_total"],415)
        self.assertEqual(x["quality_gate"],"REVIEW_REQUIRED")
    def test_inconsistent_total(self):
        rows=[{"reference":"E1","metadata":{"title":["A"]}},{"reference":"E2","metadata":{"title":["B"]}}]
        x=quality_report("eu",rows,source_complete=False,reported_total=1)
        self.assertEqual(x["rejection_reasons"],{"reported_total_inconsistent":1})
        self.assertEqual(x["rejected_rows"],0)
    def test_invalid_input_shape(self):
        x=quality_report("uk",[None,[],"unexpected"],source_complete=True)
        self.assertEqual(x["rejection_reasons"],{"invalid_record_shape":3})
    def test_unsafe_input_rejected(self):
        with self.assertRaises(ValueError):
            quality_report("bad",[],source_complete=True)
        with self.assertRaises(ValueError):
            quality_report("uk",[],source_complete="true")
    def test_kind_unclassified_kept_hold(self):
        x=quality_report("eu",[{"reference":"E1","metadata":{"title":["ABC"],"type":["999"]}}],source_complete=False)
        self.assertEqual(x["candidate_kinds"],{"eu_unclassified":1})
        self.assertEqual(x["defence_relevance_verified"],0)
        self.assertFalse(x["public_release"])

if __name__=="__main__":
    unittest.main()
