import importlib.util
import pathlib
import unittest

PATH = pathlib.Path(__file__).resolve().parents[1] / "collectors/research/openalex.py"
spec = importlib.util.spec_from_file_location("bohun_openalex", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class OpenAlexTests(unittest.TestCase):
    def test_normalize(self):
        result = module.normalize({"id": "https://openalex.org/W1", "display_name": "Research",
            "doi": None, "publication_date": "2026-10-01", "primary_location": {"source": {"display_name": "Journal"}}}, "radar")
        self.assertEqual(result["signal_type"], "innovation_rd")
        self.assertEqual(result["publisher_source"], "Journal")
        self.assertFalse(result["is_verified_relevance"])

    def test_missing_identifier(self):
        self.assertIsNone(module.normalize({"display_name": "Research"}, "radar"))

    def test_null_location(self):
        self.assertIsNone(module.normalize({"id": "W1", "display_name": ""}, "radar"))
        self.assertEqual(module.normalize({"id": "W1", "display_name": "Title", "primary_location": None}, "radar")["publisher_source"], None)

if __name__ == "__main__":
    unittest.main()
