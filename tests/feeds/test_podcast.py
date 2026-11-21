"""A5TC-04 podcasts without network: episode parsing, transcript formats, the Whisper request, the audio budget,
no key, the publisher transcript first, and the collector skipping episodes already in the base."""
import datetime as dt
import unittest
import urllib.error

from collectors.feeds import collect as F
from collectors.feeds import podcast as P
from tests.feeds.test_collect import NOW, Net, Resp

LONG = "Army orders new radars for air defence units. " * 10
FRESH = "Thu, 19 Nov 2026 08:00:00 GMT"


def feed(items):
    body = "".join(
        f"<item><title>{t}</title><link>{l}</link><guid>{l}</guid><pubDate>{FRESH}</pubDate>"
        f"<description>Show notes</description>"
        f'<enclosure url="{a}" type="audio/mpeg" length="1"/><itunes:duration>{d}</itunes:duration>{x}</item>'
        for t, l, a, d, x in items)
    return ('<?xml version="1.0"?><rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" '
            'xmlns:podcast="https://podcastindex.org/namespace/1.0"><channel><title>Pod</title>'
            f"{body}</channel></rss>").encode()


class FakeFetcher:
    def __init__(self, pages, blocked=()):
        self.pages, self.blocked, self.got = pages, blocked, []

    def allowed(self, url):
        return url not in self.blocked

    def _get(self, url):
        self.got.append(url)
        return self.pages.get(url, (404, {}, b""))


class Podcast(unittest.TestCase):
    def test_duration(self):
        self.assertEqual([P.duration_seconds(x) for x in ("3725", "1:02:05", "62:05", "", None, "abc", "1:x")],
                         [3725, 3725, 3725, None, None, None, None])

    def test_episodes_audio_only_with_transcripts(self):
        tr = '<podcast:transcript url="https://p.example/1.vtt" type="text/vtt"/>'
        body = feed([("Ep1", "https://p.example/1", "https://cdn.example/1.mp3", "30:00", tr),
                     ("Ep2", "https://p.example/2", "https://cdn.example/2.mp3", "1:00:00", "")])
        body = body.replace(b"</channel>", b"<item><title>Text</title><link>https://p.example/t</link></item></channel>")
        eps = P.episodes(body)
        self.assertEqual(eps["https://p.example/1"], {"audio": "https://cdn.example/1.mp3", "duration": 1800,
                                                      "transcripts": [("https://p.example/1.vtt", "text/vtt")]})
        self.assertEqual(eps["https://p.example/2"]["duration"], 3600)
        self.assertNotIn("https://p.example/t", eps)
        self.assertEqual(P.episodes(b"not xml <"), {})

    def test_transcript_formats(self):
        vtt = b"WEBVTT\n\nNOTE x\n\n1\n00:00:01.000 --> 00:00:04.000\n<v Host>Hello <b>world</b>\n\n2\n" \
              b"00:00:05.000 --> 00:00:06.000\nSecond line\n"
        self.assertEqual(P.transcript_to_text(vtt, "text/vtt"), "Hello world Second line")
        srt = b"1\n00:00:01,000 --> 00:00:02,000\nFirst\n\n2\n00:00:03,000 --> 00:00:04,000\nNext\n"
        self.assertEqual(P.transcript_to_text(srt, "application/x-subrip"), "First Next")
        js = b'{"version":"1.0.0","segments":[{"speaker":"A","body":"One."},{"body":"Two."},"bad"]}'
        self.assertEqual(P.transcript_to_text(js, "application/json"), "One. Two.")
        self.assertEqual(P.transcript_to_text(b"{broken", "application/json"), "")
        self.assertEqual(P.transcript_to_text(b"<html><body><p>Hi</p><script>x()</script></body></html>",
                                              "text/html").strip(), "Hi")
        self.assertEqual(P.transcript_to_text(b"  plain  ", "text/plain"), "plain")

    def test_whisper_request_sends_url_not_audio(self):
        sent = {}

        def opener(req, timeout):
            sent.update(url=req.full_url, body=req.data.decode(), auth=req.get_header("Authorization"),
                        ctype=req.get_header("Content-type"), timeout=timeout)
            return Resp(200, b" recognised text \n")
        out = P.whisper("https://cdn.example/1.mp3", "k", opener=opener)
        self.assertEqual(out, "recognised text")
        self.assertEqual(sent["url"], P.WHISPER_URL)
        self.assertEqual(sent["auth"], "Bearer k")
        self.assertTrue(sent["ctype"].startswith("multipart/form-data; boundary="))
        for part in ('name="url"\r\n\r\nhttps://cdn.example/1.mp3', 'name="model"', 'name="response_format"\r\n\r\ntext'):
            self.assertIn(part, sent["body"])
        self.assertNotIn('name="file"', sent["body"])

    def test_publisher_transcript_first(self):
        f = FakeFetcher({"https://p.example/1.txt": (200, {}, LONG.encode())})
        calls = []
        t = P.Transcriber(f, key="k", whisper_fn=lambda a, k: calls.append(a) or LONG)
        text, src = t.text({"audio": "https://cdn.example/1.mp3", "duration": 600,
                            "transcripts": [("https://p.example/1.txt", "text/plain")]})
        self.assertEqual((text, src["source"]), (LONG.strip(), "publisher"))
        self.assertEqual(calls, [])
        self.assertEqual(t.counts["publisher"], 1)

    def test_blocked_or_short_transcript_falls_back_to_whisper(self):
        f = FakeFetcher({"https://p.example/1.txt": (200, {}, b"short")}, blocked={"https://p.example/2.txt"})
        t = P.Transcriber(f, key="k", budget=1000, whisper_fn=lambda a, k: LONG)
        text, src = t.text({"audio": "https://cdn.example/1.mp3", "duration": 600,
                            "transcripts": [("https://p.example/2.txt", "text/plain"),
                                            ("https://p.example/1.txt", "text/plain")]})
        self.assertEqual(src["source"], "whisper")
        self.assertEqual(src["seconds"], 600)
        self.assertNotIn("https://p.example/2.txt", f.got)
        self.assertEqual(t.budget, 400)

    def test_budget_long_episode_no_key_and_errors(self):
        ep = {"audio": "https://cdn.example/1.mp3", "duration": 600, "transcripts": []}
        t = P.Transcriber(FakeFetcher({}), key="k", budget=1000, whisper_fn=lambda a, k: LONG)
        self.assertIsNotNone(t.text(ep)[0])
        self.assertEqual(t.text(ep), (None, None))  # 400 s left, episode is 600 s
        self.assertEqual(t.text({**ep, "duration": None}), (None, None))  # unknown length counts as an hour
        big = P.Transcriber(FakeFetcher({}), key="k", budget=10**6, whisper_fn=lambda a, k: LONG)
        self.assertEqual(big.text({**ep, "duration": P.MAX_EPISODE_SECONDS + 1}), (None, None))
        nokey = P.Transcriber(FakeFetcher({}), key=None, whisper_fn=lambda a, k: self.fail("no call without key"))
        self.assertEqual(nokey.text(ep), (None, None))
        self.assertEqual(nokey.counts["skipped_budget"], 1)

        def boom(a, k):
            raise urllib.error.URLError("429")
        err = P.Transcriber(FakeFetcher({}), key="k", budget=1000, whisper_fn=boom)
        self.assertEqual(err.text(ep), (None, None))
        self.assertEqual((err.counts["failed"], err.budget), (1, 1000))

    def test_long_cyrillic_text_is_cut_not_dropped(self):
        text = "Слово " * 40000  # ~440 KB in UTF-8
        cap = P.as_capture(text, NOW)
        self.assertEqual(cap["status"], "BODY_PRESENT_COMPLETENESS_UNVERIFIED")
        self.assertLessEqual(cap["text_bytes"], P.MAX_TEXT_BYTES)
        self.assertTrue(cap["text"].startswith("Слово"))

    def test_collector_transcribes_new_episodes_only(self):
        body = feed([("Ep1", "https://p.example/1", "https://cdn.example/1.mp3", "10:00", ""),
                     ("Ep2", "https://p.example/2", "https://cdn.example/2.mp3", "10:00", "")])
        net = Net({"https://p.example/robots.txt": (404, b"", {}), "https://p.example/feed": (200, body, {})})
        fetcher = F.Fetcher(opener=net, sleep=lambda s: None)
        heard = []
        t = P.Transcriber(fetcher, key="k", whisper_fn=lambda a, k: heard.append(a) or LONG)
        done = frozenset({F.external_id("https://p.example/2", None)})
        _, shadow = F.poll([{"channel_id": "p", "url": "https://p.example/feed"}], fetcher, NOW,
                           transcriber=t, done=done)
        by = {s["url"]: s for s in shadow}
        self.assertEqual(heard, ["https://cdn.example/1.mp3"])
        self.assertEqual(by["https://p.example/1"]["transcript"]["source"], "whisper")
        self.assertGreater(by["https://p.example/1"]["text_chars"], 400)
        self.assertIsNone(by["https://p.example/2"]["transcript"])
        _, plain = F.poll([{"channel_id": "p", "url": "https://p.example/feed"}],
                          F.Fetcher(opener=net, sleep=lambda s: None), NOW)
        self.assertTrue(all(s["transcript"] is None for s in plain))  # shadow mode: no recognition


if __name__ == "__main__":
    unittest.main()
