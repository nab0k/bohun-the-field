import unittest
from collectors.source_registry.namc_directory import namc_items, domain

class DirectoryExtractionTests(unittest.TestCase):
    def test_links_and_unique_hosts(self):
        html = """<a href="https://alpha.example/news">Visit Website</a>
                  <a href="https://alpha.example/about">Visit Website</a>
                  <a href="https://beta.example">Visit Website</a>
                  <a href="https://linkedin.com/company/something">Visit Website</a>
                  <a href="https://reject.ru">Visit Website</a>"""
        rows = namc_items(html, "https://www.namconsortium.org/membership/member-directory")
        self.assertEqual({x["hostname"] for x in rows}, {"alpha.example", "beta.example"})
        self.assertTrue(all(x["status"] == "candidate_unverified" for x in rows))
    def test_host_exclusions(self):
        self.assertEqual(domain("https://www.linkedin.com/company/example"), "")
        self.assertEqual(domain("https://business.example/page"), "business.example")

if __name__ == "__main__":
    unittest.main()
