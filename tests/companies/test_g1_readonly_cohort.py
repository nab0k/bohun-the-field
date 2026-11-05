"""G1 mock tests: fail-closed Supabase readback, no DB writes."""
import unittest
from scripts.g1_readonly_cohort import IDS, read_cohort

class FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls=[]
    def execute(self, sql, args=None):
        self.calls.append(sql)
        if "SELECT d.id" in sql:
            return self
        return self
    def fetchall(self): return self.rows

def fixture():
    rows=[]
    for i,n in enumerate(sorted(IDS)):
        status="discovered" if i<12 else "candidate_existing" if i<18 else "hold"
        rows.append({"id":n,"name":"Test Company "+str(n),"reported_country":"SOURCE COUNTRY",
                     "status":status,"matched_company_id":"co_test" if status=="candidate_existing" else None,
                     "source":"eurosatory_2024_exhibitors"})
    return rows

class G1ReadOnlyTest(unittest.TestCase):
    def test_frozen_20(self):
        db=FakeDB(fixture())
        result=read_cohort(db)
        self.assertEqual(len(result["records"]),20)
        self.assertFalse(result["public_release"])
        self.assertEqual(result["qa"],"HOLD")
        self.assertEqual(db.calls[0],"SET TRANSACTION READ ONLY")
        self.assertTrue(all("INSERT" not in s and "UPDATE" not in s and "DELETE" not in s for s in db.calls))
    def test_missing_row_stops(self):
        with self.assertRaises(RuntimeError):
            read_cohort(FakeDB(fixture()[:-1]))
    def test_source_drift_stops(self):
        rows=fixture();rows[0]["source"]="other"
        with self.assertRaises(RuntimeError):
            read_cohort(FakeDB(rows))
    def test_status_drift_stops(self):
        rows=fixture();rows[0]["status"]="hold"
        with self.assertRaises(RuntimeError):
            read_cohort(FakeDB(rows))

if __name__=="__main__": unittest.main()
