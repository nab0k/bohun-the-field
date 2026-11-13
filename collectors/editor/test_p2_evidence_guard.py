"""Synthetic offline C3-10 guard checks. No private fixtures, DB, provider or network."""
import copy
import json
import unittest
from collectors.editor.evidence_guard import check_claim_p2
from collectors.editor.p2_guard_integration import assess_p2_packet

URL = "https://example.org/fictional"


def packet():
    return {"schema":"bohun-c3-10-checker-probe-v1",
            "claims":[{"claim_id":"x","text":"ACME acquired 75% of shares."},
                      {"claim_id":"y","text":"The US government ordered drones."}],
            "spans":[{"source_id":"p2","span_id":"s1","url":URL,
                      "quote":"ACME acquired 50 Prozent der Anteile."},
                     {"source_id":"p2","span_id":"s2","url":URL,
                      "quote":"A government ordered the drones."}]}


def codes(claim, *quotes):
    return [f["code"] for f in check_claim_p2(
        claim,[{"span_id":str(i),"text":q} for i,q in enumerate(quotes)])["findings"]]


class P2GuardTests(unittest.TestCase):
    def test_percent_conflict(self):
        self.assertEqual(codes("ACME acquired 75% stake.", "ACME acquired 50 Prozent equity."),
                         ["NUMERIC_PERCENT_CONFLICT_POSSIBLE"])
    def test_matching_percent(self):
        self.assertEqual(codes("ACME acquired 50% stake.", "ACME acquired 50 Prozent equity."), [])
    def test_other_actor_not_conflict(self):
        self.assertEqual(codes("ACME acquired 75% stake.", "OTHER acquired 50% equity."), [])
    def test_other_quantity_not_conflict(self):
        self.assertEqual(codes("ACME ordered 5000 drones.", "ACME ordered 4000 drones."), [])
    def test_other_context_not_conflict(self):
        self.assertEqual(codes("ACME achieved 75% efficiency.", "ACME acquired 50% equity."), [])
    def test_matching_alternative_quote(self):
        self.assertEqual(codes("ACME acquired 50% stake.","ACME acquired 75% stake.",
                               "ACME acquired 50% stake."), [])
    def test_us_attribution_missing(self):
        self.assertEqual(codes("The US government bought drones.","A government bought drones."),
                         ["US_GOVERNMENT_ATTRIBUTION_NOT_EXPLICIT"])
    def test_us_attribution_explicit(self):
        self.assertEqual(codes("The U.S. government bought drones.",
                               "The United States government bought drones."), [])
    def test_named_ship_missing(self):
        self.assertEqual(codes("HMS Aurora completed launches.","An aircraft carrier launched drones."),
                         ["NAMED_SHIP_NOT_EXPLICIT"])
    def test_named_exercise_missing(self):
        self.assertEqual(codes("Exercise Northern Bridge B began.", "The naval exercise began."),
                         ["NAMED_EXERCISE_NOT_EXPLICIT"])
    def test_no_overcapture_of_verb(self):
        self.assertEqual(codes("HMS Aurora completed three launches.",
                               "HMS Aurora completed three launches."), [])
    def test_reject_bad_span(self):
        with self.assertRaises(ValueError):
            check_claim_p2("ACME", [{"span_id":"s","text":"x"},{"span_id":"s","text":"x"}])
    def test_bridge_metadata_only(self):
        p=packet()
        before=copy.deepcopy(p)
        result=assess_p2_packet(p,expected_p2_url=URL)
        self.assertEqual(result["flagged_claims"],2)
        self.assertEqual(result["span_count"],2)
        self.assertFalse(result["publication_authorized"])
        self.assertNotIn("ACME",json.dumps(result))
        self.assertEqual(p,before)
    def test_foreign_source_ignored(self):
        p=packet()
        p["spans"].append({"source_id":"foreign","span_id":"f","url":"https://elsewhere.org",
                            "quote":"The US government ordered drones."})
        r=assess_p2_packet(p,expected_p2_url=URL)
        self.assertEqual(r["excluded_foreign_spans"],1)
        self.assertEqual(r["flagged_claims"],2)
    def test_mismatched_source_cannot_suppress_flag(self):
        p=packet()
        p["spans"][1]["url"]="https://elsewhere.org"
        r=assess_p2_packet(p,expected_p2_url=URL)
        self.assertEqual(r["flagged_claims"],2)
    def test_no_linked_source_fails_closed(self):
        p=packet()
        for s in p["spans"]: s["url"]="https://elsewhere.org"
        with self.assertRaisesRegex(ValueError,"missing_or_multiple"):
            assess_p2_packet(p,expected_p2_url=URL)
    def test_two_linked_source_ids_rejected(self):
        p=packet()
        p["spans"][1]["source_id"]="other"
        with self.assertRaisesRegex(ValueError,"multiple_linked"):
            assess_p2_packet(p,expected_p2_url=URL)
    def test_duplicate_ids_rejected(self):
        p=packet();p["claims"][1]["claim_id"]="x"
        with self.assertRaisesRegex(ValueError,"invalid_claim"):
            assess_p2_packet(p,expected_p2_url=URL)
    def test_bad_url_rejected(self):
        with self.assertRaisesRegex(ValueError,"invalid_p2_guard_url"):
            assess_p2_packet(packet(),expected_p2_url="http://example.org/fictional")
    def test_missing_spans_rejected(self):
        p=packet();p["spans"]=[]
        with self.assertRaisesRegex(ValueError,"invalid_p2_guard_counts"):
            assess_p2_packet(p,expected_p2_url=URL)


if __name__ == "__main__":
    unittest.main()
