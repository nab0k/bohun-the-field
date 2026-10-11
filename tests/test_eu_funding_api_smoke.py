"""Offline regression checks for EU Search API request encoding."""
import json
import unittest
from collectors.opportunities.eu_funding_api_smoke import multipart, query_data, EDF_PROGRAMME

class TestEUFundingSmoke(unittest.TestCase):
    def test_edf_filter_present(self):
        q = query_data()
        self.assertIn(EDF_PROGRAMME, json.dumps(q))
        self.assertIn("frameworkProgramme", json.dumps(q))

    def test_multipart_query_json(self):
        q = query_data()
        body, mime = multipart({"query": q, "languages": ["en"]})
        boundary = mime.split("boundary=", 1)[1]
        self.assertTrue(mime.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="query"', body)
        self.assertIn(b"Content-Type: application/json", body)
        self.assertIn(json.dumps(q, separators=(",", ":")).encode(), body)
        self.assertTrue(body.endswith(("--" + boundary + "--\r\n").encode()))

    def test_different_boundaries(self):
        self.assertNotEqual(multipart({"query": {}})[1], multipart({"query": {}})[1])

if __name__ == "__main__":
    unittest.main()
