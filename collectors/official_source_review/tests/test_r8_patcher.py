"""Verify the R7.1 -> R8 review patch without carrying a local-only patcher.

These checks are executable alongside PR #77 code in the review branch.
"""
import inspect
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uk_staging_writer as w


class R8WriterContractTests(unittest.TestCase):
    def test_new_reader_requires_bound_updated_window(self):
        src = inspect.getsource(w.validate_manifest)
        self.assertIn("require_bound_receipt", src)
        self.assertNotIn("candidate_outside_receipt_window", src)

    def test_default_live_path_uses_complete_r8_reader(self):
        src = inspect.getsource(w.write_live_window)
        self.assertIn("prepare_window", src)
        self.assertIn("make_window", src)
        self.assertNotIn("prepare_bounded_live", src)


if __name__ == "__main__":
    unittest.main()
