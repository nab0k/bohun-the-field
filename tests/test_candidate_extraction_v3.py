import unittest
from tools.candidate_extraction_v3 import extract

class ExtractionTests(unittest.TestCase):
    def test_jsonld_organization(self):
        html = '<script type="application/ld+json">{"@type":"Organization","name":"Aero Example","url":"https://aero-example.test"}</script>'
        result = extract(html, "https://expo.example/exhibitors")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Aero Example")
        self.assertEqual(result[0]["evidence_type"], "jsonld")

    def test_directory_card(self):
        html = '<div class="exhibitor-card"><h3>Naval Systems</h3><a href="https://naval.example/">Website</a></div>'
        result = extract(html, "https://expo.example/exhibitors")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Naval Systems")

    def test_reject_social_and_internal(self):
        html = '<div class="exhibitor-card"><h3>Test</h3><a href="https://linkedin.com/company/test">Social</a><a href="/profile">Profile</a></div>'
        self.assertEqual(extract(html, "https://expo.example/exhibitors"), [])

if __name__ == "__main__":
    unittest.main()
