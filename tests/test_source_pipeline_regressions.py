import unittest
from collectors.web_monitor.monitor import classify_candidate
from collectors.source_registry.reconcile import build

class PipelineRegressionTests(unittest.TestCase):
    def test_monitor_rejects_pdf(self):
        self.assertEqual(classify_candidate("https://example.org/news/report.pdf", "https://example.org"), "reject")
    def test_monitor_recognizes_date(self):
        self.assertEqual(classify_candidate("https://example.org/2026/10/09/item", "https://example.org"), "possible_article")
    def test_registry_does_not_confuse_seed_with_active_monitor(self):
        sources = [{"name":"Example","url":"https://example.org","group":"Media"}]
        report = {"results":[{"name":"Example","status":"validated_feed","feed_url":"https://example.org/feed"}]}
        channels = [{"url":"https://example.org/news","status":"seed_unverified"}]
        rows = build(sources, report, channels)
        self.assertEqual(rows[0]["next_action"],"review_feed_for_import")
        self.assertEqual(rows[0]["web_monitor_state"],"unverified_seed")
if __name__ == "__main__":
    unittest.main()
