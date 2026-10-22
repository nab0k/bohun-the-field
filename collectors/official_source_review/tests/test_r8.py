import json
import unittest
from datetime import datetime, timezone, timedelta
from urllib.parse import parse_qs, urlsplit

from r8_source_reader import (
    API, WindowError, make_window, plan_windows, read_window, group_lifecycle,
)
from r8_checkpoint_rules import CoverageError, proposed_checkpoint


def release(n, date="2021-02-01T14:00:00Z", tags=("tender",)):
    return {"ocid": "ocds-h6vhtk-test", "id": f"{n:06d}-2021", "date": date,
            "tag": list(tags), "tender": {"title": f"Item {n}"}}


def page(rows, next_link=None):
    return json.dumps({"releases": rows, "links": {"next": next_link} if next_link else {}},
                      separators=(",", ":")).encode()


class WindowTests(unittest.TestCase):
    def test_real_october_window_maps_to_utc(self):
        w=make_window("2026-10-15T09:00:00", "2026-10-15T10:00:00")
        self.assertEqual(w.utc_from.isoformat(), "2026-10-15T08:00:00+00:00")
    def test_winter_gmt(self):
        self.assertEqual(make_window("2026-01-15T09:00:00", "2026-01-15T10:00:00").utc_from.hour, 9)
    def test_reject_ambiguous_fall(self):
        with self.assertRaises(WindowError):make_window("2026-10-25T01:00:00", "2026-10-25T01:30:00")
    def test_reject_spring_gap(self):
        with self.assertRaises(WindowError):make_window("2026-03-29T01:00:00", "2026-03-29T01:30:00")
    def test_reject_crossing_dst(self):
        with self.assertRaises(WindowError):make_window("2026-03-29T00:45:00", "2026-03-29T02:15:00")
    def test_reject_nonpositive(self):
        with self.assertRaises(WindowError):make_window("2026-10-15T10:00:00", "2026-10-15T09:00:00")
    def test_planner_overlaps(self):
        a=datetime(2026,10,15,8,tzinfo=timezone.utc)
        r=plan_windows(a,a+timedelta(hours=1),minutes=15)
        self.assertEqual(len(r),4)
        self.assertEqual(r[1].utc_from, r[0].utc_to-timedelta(seconds=1))
    def test_planner_stops_at_dst(self):
        a=datetime(2026,10,25,0,tzinfo=timezone.utc)
        with self.assertRaises(WindowError):plan_windows(a,a+timedelta(hours=2),minutes=15)
    def test_planner_fails_count_cap(self):
        a=datetime(2026,10,15,8,tzinfo=timezone.utc)
        with self.assertRaises(WindowError):plan_windows(a,a+timedelta(hours=1),max_windows=1)


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.w=make_window("2026-10-15T09:00:00", "2026-10-15T10:00:00")
    def test_original_published_date_before_updated_window_accepted(self):
        r=read_window(self.w,lambda u:page([release(1, "2021-02-01T14:00:00Z")]))
        self.assertTrue(r['complete']);self.assertEqual(r['unique_releases'],1)
        self.assertFalse(r['publication_window_enforced'])
    def test_empty_window_is_complete(self):
        self.assertEqual(read_window(self.w,lambda u:page([]))['unique_releases'],0)
    def test_two_pages_and_frozen_scope(self):
        urls=[]
        def transport(u):
            urls.append(u)
            if len(urls)==1:
                return page([release(1)], API+'?updatedFrom=2026-10-15T09%3A00%3A00&updatedTo=2026-10-15T10%3A00%3A00&limit=100&cursor=MTAwM%3D%3D')
            return page([release(2)])
        r=read_window(self.w,transport)
        self.assertEqual(r['pages'],2)
        q=parse_qs(urlsplit(urls[-1]).query)
        self.assertEqual(q['updatedFrom'], [self.w.local_from])
        self.assertEqual(q['cursor'],['MTAwM=='])
    def test_reject_host_changed(self):
        with self.assertRaises(WindowError):read_window(self.w,lambda u:page([],"https://evil.invalid/ocdsReleasePackages?cursor=AAA"))
    def test_reject_scope_change(self):
        with self.assertRaises(WindowError):read_window(self.w,lambda u:page([], API+'?updatedFrom=2026-10-14T09:00:00&cursor=AAA'))
    def test_reject_extra_params(self):
        with self.assertRaises(WindowError):read_window(self.w,lambda u:page([], API+'?cursor=AAA&debug=1'))
    def test_reject_duplicate_cursor_params(self):
        with self.assertRaises(WindowError):read_window(self.w,lambda u:page([], API+'?cursor=AAA&cursor=BBB'))
    def test_cursor_loop_detected(self):
        with self.assertRaises(WindowError):read_window(self.w,lambda u:page([release(1)], API+'?cursor=AAA'),max_pages=4)
    def test_limit_exhaustion_not_complete(self):
        with self.assertRaises(WindowError):read_window(self.w,lambda u:page([release(1)],API+'?cursor=AAA'),max_pages=1)
    def test_duplicate_identical_release(self):
        r=read_window(self.w,lambda u:page([release(1),release(1)]))
        self.assertEqual((r['unique_releases'],r['duplicate_releases']),(1,1))
    def test_same_identity_changed_payload_fails(self):
        with self.assertRaises(WindowError):read_window(self.w,lambda u:page([release(1),release(1,tags=('award',))]))
    def test_excessive_page(self):
        with self.assertRaises(WindowError):read_window(self.w,lambda u:page([release(i) for i in range(101)]))
    def test_invalid_json_fails(self):
        with self.assertRaises(WindowError):read_window(self.w,lambda u:b'broken')
    def test_transport_failure_propagates(self):
        def transport(url): raise TimeoutError('no network')
        with self.assertRaises(TimeoutError):read_window(self.w,transport)
    def test_lifecycle_keeps_tags_without_inferred_closure(self):
        g=group_lifecycle([release(1),release(2,tags=('award',))])
        self.assertEqual(len(g['ocds-h6vhtk-test']),2)
        self.assertFalse(g['ocds-h6vhtk-test'][0]['status_verified'])


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.t=datetime(2026,10,15,8,tzinfo=timezone.utc)
    def rec(self,start,end,status="COMPLETE",commit=True,complete=True):
        return {"window_start":start,"window_end":end,"status":status,
                "source_window_complete":complete,"transaction_committed":commit}
    def test_contiguous_two_receipts(self):
        t=self.t
        self.assertEqual(proposed_checkpoint(t,[self.rec(t,t+timedelta(minutes=15)),
            self.rec(t+timedelta(minutes=15)-timedelta(seconds=1),t+timedelta(minutes=30))]),t+timedelta(minutes=30))
    def test_failed_receipt_blocks(self):
        with self.assertRaises(CoverageError):proposed_checkpoint(self.t,[self.rec(self.t,self.t+timedelta(minutes=15),status="FAILED")])
    def test_uncommitted_blocks(self):
        with self.assertRaises(CoverageError):proposed_checkpoint(self.t,[self.rec(self.t,self.t+timedelta(minutes=15),commit=False)])
    def test_partial_source_blocks(self):
        with self.assertRaises(CoverageError):proposed_checkpoint(self.t,[self.rec(self.t,self.t+timedelta(minutes=15),complete=False)])
    def test_gap_blocks(self):
        with self.assertRaises(CoverageError):proposed_checkpoint(self.t,[self.rec(self.t+timedelta(seconds=1),self.t+timedelta(minutes=15))])
    def test_wrong_order_blocks(self):
        t=self.t
        with self.assertRaises(CoverageError):proposed_checkpoint(t,[self.rec(t,t+timedelta(minutes=15)),self.rec(t,t+timedelta(minutes=15))])

if __name__ == '__main__':unittest.main()
