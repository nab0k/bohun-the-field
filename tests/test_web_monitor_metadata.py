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


    def test_json_ld_article_date_and_headline(self):
        html = '<html><head><script type="application/ld+json">{"@type":"NewsArticle","headline":"German defence announcement","datePublished":"2026-10-01T12:00:00+02:00"}</script></head></html>'
        item = {'url':'https://example.org/news/german-announcement','quality':'possible_article'}
        with patch('collectors.web_monitor.monitor.get',return_value=(html,item['url'])):
            result = audit_candidate(item,'https://example.org/',{})
        self.assertTrue(result['verified'],result)
        self.assertEqual(result['title'],'German defence announcement')

    def test_json_ld_graph(self):
        html = '<script type="application/ld+json">{"@graph":[{"@type":"WebSite","name":"Home"},{"@type":"NewsArticle","headline":"Article from graph","datePublished":"2026-10-01T12:00:00Z"}]}</script>'
        item = {'url':'https://example.org/news/graph-story','quality':'possible_article'}
        with patch('collectors.web_monitor.monitor.get',return_value=(html,item['url'])):
            result = audit_candidate(item,'https://example.org/',{})
        self.assertTrue(result['verified'],result)


if __name__ == '__main__':
    unittest.main()
