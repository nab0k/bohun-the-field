import io,json,tempfile,unittest,zipfile
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from global_harvester import records_zip, FeedLinks, norm
class TestHarvester(unittest.TestCase):
 def test_archive_dedup_and_safe_status(self):
  with tempfile.TemporaryDirectory() as d:
   stream=io.BytesIO()
   with zipfile.ZipFile(stream,"w") as z:
    z.writestr("organization.csv","name,country,organisationID,projectID\nACME Aerospace,GB,10,101\nACME Aerospace,GB,10,102\nDifferent Entity,FR,20,103\n")
   result=records_zip(stream.getvalue(),"cordis-horizon",{norm("ACME Aerospace")},Path(d))
   self.assertEqual(result["organization_rows"],3)
   self.assertEqual(result["unique_source_records"],2)
   self.assertEqual(result["matched_existing_name"],1)
   rows=[json.loads(x) for x in (Path(d)/"cordis-horizon.jsonl").read_text().splitlines()]
   self.assertTrue(all(x["publication_approved"] is False and x["qa_status"]=="PENDING" for x in rows))
 def test_feed_tags(self):
  p=FeedLinks();p.feed('<link rel="alternate" type="application/rss+xml" href="/feed.xml"><link rel="alternate" type="text/css" href="x.css">')
  self.assertEqual(p.links,["/feed.xml"])
if __name__=="__main__":unittest.main()
