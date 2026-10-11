"""Read-only metadata regression tests for Web Monitor."""
import unittest
from unittest.mock import patch
from collectors.web_monitor.monitor import audit_candidate, classify_candidate


class MetadataTest(unittest.TestCase):
    def test_section_not_article(self):
        self.assertEqual(classify_candidate('https://example.org/news', 'https://example.org/'), 'index')

    def test_article_with_valid_metadata(self):
        html = '<html><head><title>Example article</title><meta property="article:published_time" content="2026-10-01T12:00:00+00:00"><link rel="canonical" href="/news/example-article"></head></html>'
        item = {'url':'https://example.org/news/example-article','quality':'possible_article'}
        with patch('collectors.web_monitor.monitor.get',return_value=(html,item['url'])):
            result = audit_candidate(item,'https://example.org/',{})
        self.assertTrue(result['verified'],result)

    def test_section_with_date_is_not_verified(self):
        html = '<html><head><title>News index</title><meta property="article:published_time" content="2026-10-01T12:00:00+00:00"></head></html>'
        item = {'url':'https://example.org/news','quality':'index'}
        with patch('collectors.web_monitor.monitor.get',return_value=(html,item['url'])):
            result = audit_candidate(item,'https://example.org/',{})
        self.assertFalse(result['verified'])


if __name__ == '__main__':
    unittest.main()
