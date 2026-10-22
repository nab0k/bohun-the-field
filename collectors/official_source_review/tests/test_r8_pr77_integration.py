"""Executed when R8 files are installed beside real PR77 writer and R6 normalizer.

Skipped outside the checked-out repository. It checks the changed *contract*;
prior branch tests also require adapting their obsolete manifest fixtures.
"""
import importlib.util
import json
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

@unittest.skipUnless(importlib.util.find_spec('uk_hold_pilot') and importlib.util.find_spec('uk_staging_writer'),
                     'needs real checked-out PR77 R6/R7.1 modules; offline bundle omits upstream files')
class LiveCodeIntegration(unittest.TestCase):
    def test_old_publication_inside_complete_update_window_is_valid(self):
        from r8_source_reader import make_window
        from r8_bridge import prepare_window
        import uk_hold_pilot
        import uk_staging_writer
        window=make_window('2026-10-15T09:00:00','2026-10-15T10:00:00')
        row={'ocid':'ocds-h6vhtk-123abc','id':'000123-2021','date':'2021-02-01T12:00:00Z',
             'tag':['tender'],'tender':{'title':'Synthetic old tender, updated this week'}}
        transport=lambda url:json.dumps({'releases':[row],'links':{}}).encode()
        manifest=prepare_window(window,transport,uk_hold_pilot.prepare_releases)
        clean=uk_staging_writer.validate_manifest(manifest,window_start=window.utc_from,window_end=window.utc_to)
        self.assertEqual(len(clean),1)
        self.assertEqual(clean[0][4].year,2021)
    def test_wrong_bound_source_receipt_rejected(self):
        from datetime import timedelta
        from r8_source_reader import make_window
        from r8_bridge import prepare_window
        import uk_hold_pilot
        import uk_staging_writer
        window=make_window('2026-10-15T09:00:00','2026-10-15T10:00:00')
        row={'ocid':'ocds-h6vhtk-123abc','id':'000123-2021','date':'2021-02-01T12:00:00Z',
             'tag':['tender'],'tender':{'title':'Synthetic old tender, updated this week'}}
        manifest=prepare_window(window,lambda u:json.dumps({'releases':[row],'links':{}}).encode(),uk_hold_pilot.prepare_releases)
        with self.assertRaises(uk_staging_writer.StagingError):
            uk_staging_writer.validate_manifest(manifest,window_start=window.utc_from+timedelta(hours=1),window_end=window.utc_to+timedelta(hours=1))
