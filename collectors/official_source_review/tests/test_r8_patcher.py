from pathlib import Path
import sys,unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apply_r8_to_r7 import DATE_CHECK,OLD_LIVE,RECEIPT_CHECK,NEW_LIVE,patched

class PatcherTests(unittest.TestCase):
    def test_refuses_wrong_or_already_patched_file(self):
        with self.assertRaisesRegex(ValueError,'refuse blind patch'):
            patched('def foo(): pass')
    def test_applies_exact_known_upstream_fragments_only(self):
        example='''def validate_manifest(manifest,*,window_start,window_end):\n    candidates=[]\n''' +DATE_CHECK + '''    return candidates\n\ndef write_live_window(start,end,*,dry_run=True,connect=None,transport=None):\n''' +OLD_LIVE
        s=patched(example)
        self.assertIn(RECEIPT_CHECK,s)
        self.assertIn(NEW_LIVE,s)
        self.assertNotIn('candidate_outside_receipt_window',s)
        with self.assertRaises(ValueError):patched(s)
if __name__=='__main__':unittest.main()
