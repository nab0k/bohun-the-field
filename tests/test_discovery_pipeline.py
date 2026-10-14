import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

class PipelineTests(unittest.TestCase):
    def test_registry_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "candidates.csv"
            with src.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["website", "name_hint", "directory_page"])
                w.writeheader()
                w.writerow({"website": "https://www.example.org/about", "name_hint": "Example", "directory_page": "https://expo.org"})
                w.writerow({"website": "https://example.org/", "name_hint": "Example", "directory_page": "https://expo.org"})
            cmd = [sys.executable, "tools/discovery_pipeline.py", "--inputs", str(src), "--output", str(root / "out"), "--state", str(root / "state.csv")]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            self.assertEqual((root / "out/new-sites.txt").read_text().strip(), "https://example.org/")
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            self.assertEqual((root / "out/new-sites.txt").read_text(), "")

if __name__ == "__main__":
    unittest.main()
