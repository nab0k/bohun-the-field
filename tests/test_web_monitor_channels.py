"""Offline smoke tests for publication-channel discovery."""
import unittest
from unittest.mock import patch
from collectors.web_monitor.monitor import discover_channels

class ChannelsTest(unittest.TestCase):
    def test_only_channel_articles_and_same_host(self):
        source = {"name": "Example", "url": "https://example.org/"}
        channels = [{"source": "Example", "url": "https://example.org/news", "enabled": True}]
        html = """<a href="/news">Index</a><a href="/news/story-one">Story one</a>
        <a href="/about">About</a><a href="https://evil.example/news/story-two">External</a>
        <a href="/news/file.pdf">PDF</a>"""
        with patch("collectors.web_monitor.monitor.get", return_value=(html, "https://example.org/news")):
            items, errors = discover_channels(source, {}, channels)
        self.assertEqual(errors, [])
        self.assertEqual([x["url"] for x in items], ["https://example.org/news/story-one"])
        self.assertEqual(items[0]["channel_url"], "https://example.org/news")

    def test_no_configured_channel_does_not_crawl_homepage(self):
        items, errors = discover_channels({"name":"Missing","url":"https://example.org/"}, {}, [])
        self.assertEqual((items, errors), ([], []))

if __name__ == "__main__":
    unittest.main()
