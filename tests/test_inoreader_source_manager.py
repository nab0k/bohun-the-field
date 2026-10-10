import unittest
from unittest.mock import patch
from collectors.inoreader import inoreader_source_manager as m

class SourceManagerTests(unittest.TestCase):
    def test_rss_and_atom(self):
        self.assertTrue(m.is_feed(b'<rss version="2.0"><channel><item/></channel></rss>'))
        self.assertTrue(m.is_feed(b'<feed xmlns="http://www.w3.org/2005/Atom"><entry/></feed>'))
        self.assertFalse(m.is_feed(b'<html/>'))
    def test_reject_unsafe_urls(self):
        self.assertFalse(m.safe_https("http://example.org/feed"))
        self.assertFalse(m.safe_https("https://localhost/feed"))
        self.assertFalse(m.safe_https("https://user:pass@example.org/feed"))
    def test_autodiscovery(self):
        source = {"name":"Example","url":"https://example.org","group":"Media / intelligence"}
        def fake_fetch(url):
            if url == "https://example.org":
                return (b'<link rel="alternate" type="application/rss+xml" href="/feed.xml">',url,"text/html")
            if url == "https://example.org/feed.xml":
                return (b'<rss><channel><item/></channel></rss>',url,"application/rss+xml")
            raise AssertionError(url)
        with patch.object(m, "fetch", side_effect=fake_fetch):
            result = m.discover(source)
        self.assertEqual(result["feed_url"],"https://example.org/feed.xml")
        self.assertEqual(result["status"],"validated_feed")

if __name__ == "__main__":
    unittest.main()
