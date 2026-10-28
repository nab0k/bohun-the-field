"""Offline boundaries plus optional real, pinned Trafilatura on synthetic HTML."""
import importlib.metadata
from datetime import datetime,timezone
import hashlib
from unittest import TestCase, skipUnless
from unittest.mock import Mock,patch
from collectors.web_monitor.content import capture,extract_html,WebPolicy,MAX_HTML,MAX_TEXT,NoRedirect,_public_host

URL='https://example.org/news/one'

try:
    REAL_EXTRACTOR = importlib.metadata.version('trafilatura') == '2.3.1'
except importlib.metadata.PackageNotFoundError:
    REAL_EXTRACTOR = False


@skipUnless(REAL_EXTRACTOR, 'Run in the separately approved Trafilatura 2.3.1 venv')
class RealExtractorTests(TestCase):
    def test_article_unicode_and_table_survive_navigation_and_comments_do_not(self):
        body = ('A synthetic procurement announcement describes replacement of ageing engines. '
                'The plan has not yet resulted in a signed delivery contract. ')*8
        html = ('<html><head><title>Synthetic announcement</title></head><body>'
                '<nav><a href="/login">NAVIGATION_SENTINEL_LOGIN</a></nav><main><article>'
                '<h1>Synthetic announcement</h1><p>'+body+'</p>'
                '<p>Український текст: це лише синтетична перевірка вилучення.</p>'
                '<table><tr><th>Measure</th><th>Value</th></tr>'
                '<tr><td>TABLE_SENTINEL</td><td>12</td></tr></table></article></main>'
                '<section id="comments"><p>COMMENT_SENTINEL_UNTRUSTED</p></section>'
                '<script>SCRIPT_SENTINEL()</script></body></html>')
        text = extract_html(html)
        self.assertIn('not yet resulted in a signed delivery contract', text)
        self.assertIn('Український текст', text)
        self.assertIn('TABLE_SENTINEL', text)
        for sentinel in ('NAVIGATION_SENTINEL', 'COMMENT_SENTINEL', 'SCRIPT_SENTINEL'):
            self.assertNotIn(sentinel, text)
    def test_real_extractor_capture_is_reproducible_and_does_not_grant_rights(self):
        html='<article><h1>Synthetic</h1><p>'+('Synthetic evidence remains a source claim. '*25)+'</p></article>'
        def run():
            fetch=Mock(side_effect=['User-agent: *\nAllow: /',html])
            return capture(URL,WebPolicy((URL,),'fixture-rights',True),fetch=fetch,
                           clock=lambda:datetime(2026,10,19,tzinfo=timezone.utc))
        one,two=run(),run()
        self.assertEqual(one,two)
        self.assertEqual(one['text_sha256'],hashlib.sha256(one['text'].encode()).hexdigest())
        self.assertNotIn('complete',one);self.assertNotIn('rights_verified',one)
    def test_empty_real_page_stays_unusable(self):
        fetch=Mock(side_effect=['User-agent: *\nAllow: /','<html><body></body></html>'])
        with self.assertRaises(ValueError): capture(URL,WebPolicy((URL,),'fixture',True),fetch=fetch)


class WebContentTests(TestCase):
    def test_exact_article_capture_keeps_hashes_and_no_verified_flags(self):
        fetch=Mock(side_effect=['User-agent: *\nAllow: /','<article>Body</article>'])
        result=capture(URL,WebPolicy((URL,),'reviewed-rights',True),fetch=fetch,extractor=lambda s:'Body')
        self.assertEqual(fetch.call_count,2);self.assertEqual(result['text_sha256'],hashlib.sha256(b'Body').hexdigest())
        self.assertNotIn('complete',result);self.assertNotIn('rights_verified',result)
    def test_unlisted_url_and_nonprimary_stop_without_io(self):
        fetch=Mock()
        with self.assertRaises(ValueError): capture(URL+'/other',WebPolicy((URL,),'rights',True),fetch=fetch)
        fetch.assert_not_called()
        with self.assertRaises(ValueError): WebPolicy((URL,),'rights',False)
    def test_robots_denial_stops_before_article(self):
        fetch=Mock(return_value='User-agent: *\nDisallow: /')
        with self.assertRaises(ValueError): capture(URL,WebPolicy((URL,),'rights',True),fetch=fetch)
        self.assertEqual(fetch.call_count,1)
    def test_robots_failure_does_not_fall_back_to_article(self):
        fetch=Mock(side_effect=OSError('private'))
        with self.assertRaises(OSError): capture(URL,WebPolicy((URL,),'rights',True),fetch=fetch)
        self.assertEqual(fetch.call_count,1)
    def test_empty_or_oversize_extraction_is_rejected_not_truncated(self):
        for text in (None,'','x'*(MAX_TEXT+1)):
            fetch=Mock(side_effect=['User-agent: *\nAllow: /','html'])
            with self.subTest(size=len(text or '')),self.assertRaises(ValueError):
                capture(URL,WebPolicy((URL,),'rights',True),fetch=fetch,extractor=lambda s:text)
    def test_oversize_html_is_rejected_before_extractor(self):
        extractor=Mock();fetch=Mock(side_effect=['User-agent: *\nAllow: /','x'*(MAX_HTML+1)])
        with self.assertRaises(ValueError): capture(URL,WebPolicy((URL,),'rights',True),fetch=fetch,extractor=extractor)
        extractor.assert_not_called()
    def test_redirect_is_never_followed(self):
        with self.assertRaises(ValueError): NoRedirect().redirect_request(None,None,302,'',{},'http://127.0.0.1')
    def test_private_dns_address_is_rejected(self):
        with patch('socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError): _public_host(URL)
