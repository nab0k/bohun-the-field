import csv
import json
import unittest
from pathlib import Path

class SignalLedSourceTests(unittest.TestCase):
    def test_taxonomy_crosslinks(self):
        taxonomy = json.loads(Path("collectors/source_registry/signal_taxonomy_v1.json").read_text(encoding="utf-8"))
        types = {x["id"] for x in taxonomy["types"]}
        self.assertEqual(len(types), len(taxonomy["types"]))
        with Path("docs/sources/signal-led-source-seeds-20261010.csv").open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertGreaterEqual(len(rows), 50)
        for row in rows:
            self.assertTrue(row["url"].startswith("https://"))
            self.assertTrue(set(row["signal_types"].split(";")).issubset(types), row["name"])
    def test_all_core_areas_covered(self):
        with Path("docs/sources/signal-led-source-seeds-20261010.csv").open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        groups = {x["source_family"] for x in rows}
        self.assertTrue({"defence_media","events","investment_fund","think_tank","regulation","procurement","research_institute"}.issubset(groups))
if __name__ == "__main__":
    unittest.main()
