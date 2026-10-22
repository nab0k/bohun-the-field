import copy
import unittest
from collectors.companies.private_profile_stage import compile_manifest,compile_claims,compile_cases,COHORT20,PILOT_ID

def synthetic():
    sources=[{"id":f"s{i}","url":f"https://test.invalid/{i}","original_bytes_stored":False} for i in range(6)]
    profile={"company_id":PILOT_ID,"publication":{"status":"HOLD","qa_approved":False,"public_release":False},
       "sources":sources,"evidence":[{"id":f"c{i}","path":f"/summary/f{i}","value":f"value{i}",
         "assessment":"company_claim","source_ids":["s0"]} for i in range(23)]}
    cohort={"records":[{"id":i,"name":f"Lead-{i}","reported_country":"SOURCE_ONLY",
         "status":"discovered" if j<12 else "candidate_existing" if j<18 else "hold"}
         for j,i in enumerate(sorted(COHORT20))]}
    research={"records":[{"candidate_id":r["id"],"exhibitor_name":r["name"],
         "source_reported_country_only":r["reported_country"],"discovery_status":r["status"],
         "source_leads":[{"url":"https://test.invalid/source","original_bytes_archived":False}],
         "qa_status":"HOLD","identity_binding":"NOT_APPROVED","public_release":False}
         for r in cohort["records"]]}
    return profile,research,cohort

class PrivateProfileStageTests(unittest.TestCase):
    def test_compiles_twenty_cases_and_twenty_three_claims(self):
        p,r,c=synthetic();x=compile_manifest(p,r,c)
        self.assertEqual(len(x["profiles"][0]["claims"]),23)
        self.assertEqual(len(x["research_cases"]),20)
        self.assertFalse(x["public_release"])
    def test_unknown_source_blocked(self):
        p,_,_=synthetic();p["evidence"][0]["source_ids"]=["missing"]
        with self.assertRaises(ValueError):compile_claims(p)
    def test_public_release_blocked(self):
        p,_,_=synthetic();p["publication"]["public_release"]=True
        with self.assertRaises(ValueError):compile_claims(p)
    def test_source_country_not_legal_binding(self):
        p,r,c=synthetic()
        self.assertNotIn("country",compile_claims(p)["claims"][0])
        self.assertTrue(all(x["qa_status"]=="HOLD" for x in compile_cases(r,c)))
    def test_name_drift_blocked(self):
        _,r,c=synthetic();r["records"][0]["exhibitor_name"]="Different entity"
        with self.assertRaises(ValueError):compile_cases(r,c)
    def test_unapproved_case_blocked(self):
        _,r,c=synthetic();r["records"][0]["identity_binding"]="APPROVED"
        with self.assertRaises(ValueError):compile_cases(r,c)

if __name__=="__main__":unittest.main()
