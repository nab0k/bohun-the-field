"""G2: PostgreSQL 17 migration-004 integration tests, disposable CI DB only."""
import hashlib
import os
from pathlib import Path
import unittest

DSN=os.environ.get('BOHUN_G2_TEST_POSTGRES_DSN')
if DSN:
    import psycopg
    from psycopg import errors

ROOT=Path(__file__).resolve().parents[2]
DSN=os.environ.get("BOHUN_G2_TEST_POSTGRES_DSN")
A="co_g2_test_alpha"
B="co_g2_test_beta"
H="a"*64

@unittest.skipUnless(DSN, "Only run against isolated CI PostgreSQL")
class TestG2Postgres004(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db=psycopg.connect(DSN,autocommit=True)
        name=cls.db.execute("SELECT current_database()").fetchone()[0]
        if name!="bohun_g2_ephemeral_test":
            raise RuntimeError("Refusing database not explicitly disposable: "+name)
        for role in ("anon","authenticated","service_role"):
            cls.db.execute("CREATE ROLE "+role+" NOLOGIN")
        cls.db.execute((ROOT/"db/companies/001_companies_schema.sql").read_text())
        cls.db.execute((ROOT/"db/companies/004_private_profile_claims.sql").read_text())
        cls.db.execute("INSERT INTO companies.company(id,name,name_norm,country) VALUES (%s,%s,%s,'AT'),(%s,%s,%s,'DE')",
          (A,"Synthetic Alpha GmbH","synthetic alpha gmbh",B,"Synthetic Beta GmbH","synthetic beta gmbh"))
        cls.db.execute("INSERT INTO companies.observation(source,source_key,content_hash,fetched_at,payload) VALUES ('g2_ci','fixture',%s,now(),'{}'::jsonb)",(H,))
        obs=cls.db.execute("SELECT id FROM companies.observation WHERE source='g2_ci'").fetchone()[0]
        cls.db.execute("INSERT INTO companies.discovery_candidate(observation_id,name,name_norm,status) VALUES (%s,'Synthetic Lead','synthetic lead','hold')",(obs,))
        cls.candidate_id=cls.db.execute("SELECT id FROM companies.discovery_candidate WHERE observation_id=%s",(obs,)).fetchone()[0]

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def expect_error(self,statement,args=(),exc=None):
        if exc is None:
            exc=errors.CheckViolation
        with self.assertRaises(exc):
            with self.db.transaction():
                self.db.execute(statement,args)

    def test_01_tables_keys_rls_and_permissions(self):
        expected={"profile_source","profile_claim","profile_claim_source","profile_research_case"}
        found={x[0] for x in self.db.execute("SELECT tablename FROM pg_tables WHERE schemaname='companies'")}
        self.assertTrue(expected <= found)
        for table in expected:
            name="companies."+table
            self.assertTrue(self.db.execute("SELECT relrowsecurity FROM pg_class WHERE oid=%s::regclass",(name,)).fetchone()[0])
            self.assertEqual(self.db.execute("SELECT count(*) FROM pg_policies WHERE schemaname='companies' AND tablename=%s",(table,)).fetchone()[0],0)
            for role in ("anon","authenticated","service_role"):
                for action in ("SELECT","INSERT","UPDATE","DELETE"):
                    self.assertFalse(self.db.execute("SELECT has_table_privilege(%s,%s,%s)",(role,name,action)).fetchone()[0],role+" "+name+" "+action)
        for role in ("anon","authenticated","service_role"):
            self.assertFalse(self.db.execute("SELECT has_schema_privilege(%s,'companies','USAGE')",(role,)).fetchone()[0])
        indexes={r[0] for r in self.db.execute("SELECT indexname FROM pg_indexes WHERE schemaname='companies'")}
        self.assertTrue({"profile_claim_company_path_idx","profile_case_candidate_idx"} <= indexes)

    def test_02_source_foreign_key_url_and_archiving(self):
        query="INSERT INTO companies.profile_source(company_id,source_ref,title,source_url,source_kind,as_of) VALUES (%s,'s1','Fixture',%s,'registry','2026-10-22')"
        self.expect_error(query,("unknown","https://example.org"),errors.ForeignKeyViolation)
        self.expect_error(query,(A,"http://example.org"))
        self.expect_error(query,(A,"https://bad host"))
        self.db.execute(query,(A,"https://example.org"))
        self.expect_error("UPDATE companies.profile_source SET original_bytes_archived=true WHERE company_id=%s",(A,))
        self.db.execute("UPDATE companies.profile_source SET original_bytes_archived=true,snapshot_sha256=%s,captured_at=now() WHERE company_id=%s",(H,A))

    def test_03_claim_restrictions_and_no_publication(self):
        query="INSERT INTO companies.profile_claim(company_id,claim_ref,field_path,claim_value,assessment,content_sha256) VALUES (%s,'c1',%s,'{}'::jsonb,%s,%s)"
        self.expect_error(query,(A,"bad_path","company_claim",H))
        self.expect_error(query,(A,"/products","unsupported",H))
        self.expect_error(query,(A,"/products","company_claim","bad"))
        self.db.execute(query,(A,"/products","company_claim",H))
        self.expect_error("UPDATE companies.profile_claim SET public_release=true WHERE company_id=%s",(A,))
        cid=self.db.execute("SELECT id FROM companies.profile_claim WHERE company_id=%s",(A,)).fetchone()[0]
        sid=self.db.execute("SELECT id FROM companies.profile_source WHERE company_id=%s",(A,)).fetchone()[0]
        self.db.execute("INSERT INTO companies.profile_claim_source(company_id,claim_id,source_id) VALUES (%s,%s,%s)",(A,cid,sid))
        self.db.execute("INSERT INTO companies.profile_source(company_id,source_ref,title,source_url,source_kind,as_of) VALUES (%s,'other','Second fixture','https://example.net','registry','2026-10-22')",(B,))
        other_sid=self.db.execute("SELECT id FROM companies.profile_source WHERE company_id=%s",(B,)).fetchone()[0]
        self.expect_error("INSERT INTO companies.profile_claim_source(company_id,claim_id,source_id) VALUES (%s,%s,%s)",(A,cid,other_sid),errors.ForeignKeyViolation)

    def test_04_cases_only_hold_and_valid_candidate(self):
        query="INSERT INTO companies.profile_research_case(candidate_id,research_payload,content_sha256) VALUES (%s,'{}'::jsonb,%s)"
        self.expect_error(query,(9999999,H),errors.ForeignKeyViolation)
        self.db.execute(query,(self.candidate_id,H))
        self.expect_error("UPDATE companies.profile_research_case SET qa_status='APPROVED' WHERE candidate_id=%s",(self.candidate_id,))
        self.expect_error("UPDATE companies.profile_research_case SET public_release=true WHERE candidate_id=%s",(self.candidate_id,))

    def test_05_repeat_load_is_idempotent(self):
        query="INSERT INTO companies.profile_research_case(candidate_id,research_payload,content_sha256) VALUES (%s,'{}'::jsonb,%s) ON CONFLICT DO NOTHING"
        before=self.db.execute("SELECT count(*) FROM companies.profile_research_case").fetchone()[0]
        self.db.execute(query,(self.candidate_id,H))
        self.db.execute(query,(self.candidate_id,H))
        self.assertEqual(before,self.db.execute("SELECT count(*) FROM companies.profile_research_case").fetchone()[0])

    def test_06_bad_statement_rolls_back_entire_transaction(self):
        before=self.db.execute("SELECT count(*) FROM companies.profile_claim").fetchone()[0]
        with self.assertRaises(errors.CheckViolation):
            with self.db.transaction():
                self.db.execute("INSERT INTO companies.profile_claim(company_id,claim_ref,field_path,claim_value,assessment,content_sha256) VALUES (%s,'to_roll_back','/test','{}'::jsonb,'company_claim',%s)",(A,"b"*64))
                self.db.execute("UPDATE companies.profile_claim SET public_release=true WHERE company_id=%s",(A,))
        self.assertEqual(before,self.db.execute("SELECT count(*) FROM companies.profile_claim").fetchone()[0])

    def test_07_reapplying_004_fails_without_mutation(self):
        before=self.db.execute("SELECT count(*) FROM companies.profile_claim").fetchone()[0]
        with self.assertRaises(errors.DuplicateTable):
            with self.db.transaction():
                self.db.execute((ROOT/"db/companies/004_private_profile_claims.sql").read_text())
        self.assertEqual(before,self.db.execute("SELECT count(*) FROM companies.profile_claim").fetchone()[0])

if __name__=="__main__":
    unittest.main()
