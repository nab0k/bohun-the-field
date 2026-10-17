import unittest
from unittest.mock import Mock
from tools.validate_candidate_feeds import check

class Response:
    status_code = 200
    url = "https://example.org/feed"
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_content(self, n):
        yield b"<html><body>not a feed</body></html>"

class ValidatorTests(unittest.TestCase):
    def test_html_without_feed_version_does_not_crash(self):
        session = Mock()
        session.get.return_value = Response()
        self.assertIsNone(check(session, "https://example.org/feed"))

    def test_rss_feed_is_accepted(self):
        rss = b'<rss version="2.0"><channel><title>News</title><item><title>Update</title><link>https://example.org/a</link></item></channel></rss>'
        response = Response()
        response.iter_content = lambda n: iter([rss])
        session = Mock()
        session.get.return_value = response
        result = check(session, "https://example.org/feed")
        self.assertEqual(result["entries"], 1)

if __name__ == "__main__":
    unittest.main()
