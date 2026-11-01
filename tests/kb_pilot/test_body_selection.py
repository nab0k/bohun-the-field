"""Offline integration contracts; every network entry is forbidden."""
import contextlib
import copy
from datetime import datetime, timezone
import io
import json
import socket
import sys
import types
import unittest
from unittest.mock import patch

from collectors.kb_pilot import body_selection as b, fulltext as f, pilot, embed, extract

URL = 'https://publisher.example/article'
TEXT = 'A source-bound article sentence. ' * 20
NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)


def provider(text):
    return {'version': 1, 'provider_field': 'summary.content', 'status': 'BODY_PRESENT_COMPLETENESS_UNVERIFIED',
            'text': text, 'text_sha256': b.sha(text)}


def capture(text=TEXT):
    return {'source_kind': 'publisher_article', 'capture': b.CAPTURE, 'url': URL, 'requested_url': URL,
            'final_url': URL, 'received_at': NOW.isoformat(), 'http_status': 200, 'html_sha256': 'a' * 64,
            'text_sha256': b.sha(text), 'extractor': 'trafilatura', 'extractor_version': '2.3.1',
            'text_bytes': len(text.encode()), 'characters': len(text), 'completeness': 'UNKNOWN', 'text': text}


def item(raw=None):
    return {'item_id': 1, 'source_id': 2, 'url': URL, 'raw': raw or {'content_capture': provider('short')},
            'provider_chars': 5, 'provider_present': True, 'publisher_present': False,
            'processed': False, 'embedded': False}


class Cursor:
    def __init__(self, rows=(), rowcount=1, error=False):
        self.rows, self.rowcount, self.error, self.calls = rows, rowcount, error, []
    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        if self.error: raise RuntimeError('PRIVATE_BODY_SECRET_URL')
    def fetchall(self): return self.rows
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Connection:
    def __init__(self, cursor): self.cur, self.commits, self.rollbacks = cursor, 0, 0
    def cursor(self): return self.cur
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


class Integration(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(patch.object(socket, 'socket', side_effect=AssertionError('OFFLINE')))
        self.stack.enter_context(patch.object(socket, 'getaddrinfo', side_effect=AssertionError('OFFLINE')))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
    def tearDown(self): self.stack.close()

    def test_provider_priority_and_input_immutable(self):
        raw = {'content_capture': provider(TEXT), 'publisher_capture': capture(TEXT * 2)}
        before = copy.deepcopy(raw)
        chosen = b.select_body(raw, URL)
        self.assertEqual(chosen.source_kind, 'inoreader_provider')
        self.assertEqual(chosen.text, TEXT)
        self.assertEqual(raw, before)

    def test_valid_publisher_fallback_retains_distinct_provenance(self):
        raw = {'content_capture': provider('short'), 'publisher_capture': capture()}
        result = b.select_body(raw, URL)
        self.assertEqual((result.text, result.source_kind, result.text_sha256), (TEXT, 'publisher_article', b.sha(TEXT)))
        self.assertEqual(raw['content_capture']['text'], 'short')

    def test_invalid_hash_url_version_time_and_bounds_are_blocked(self):
        cases = [('text', 'tampered'), ('url', 'https://other.example/a'), ('final_url', 'https://other.example/a'),
                 ('requested_url', 'https://other.example/a'), ('capture', 'kb-fulltext-v1'), ('http_status', 403),
                 ('extractor_version', 'other'), ('text_bytes', 1), ('characters', 1), ('html_sha256', 'x'),
                 ('received_at', 'yesterday'), ('received_at', '2026-01-01'), ('completeness', 'COMPLETE')]
        for field, value in cases:
            with self.subTest(field=field):
                cap = capture(); cap[field] = value
                self.assertIsNone(b.select_body({'publisher_capture': cap}, URL).text)
        self.assertIsNone(b.select_body({'publisher_capture': capture('я' * 70000)}, URL).text)


    def test_provider_hash_invalid_does_not_gain_trust_from_length(self):
        cap = provider(TEXT); cap['text_sha256'] = 'bad'
        self.assertIsNone(b.select_body({'content_capture': cap}, URL).text)
        self.assertIsNone(b.select_body({'content_capture': 'bad'}, URL).text)
        self.assertIsNone(b.select_body(None, URL).text)

    def test_publisher_waits_for_current_embedding_and_origin(self):
        raw = {'publisher_capture': capture()}
        current = b.sha(embed.item_text('Title', TEXT))
        self.assertEqual(b.ready_body(raw, URL, 'Title', None, 'self').reason, 'PUBLISHER_EMBEDDING_STALE_OR_MISSING')
        self.assertIsNone(b.ready_body(raw, URL, 'Title', 'old', 'self').text)
        self.assertEqual(b.ready_body(raw, URL, 'Title', current, None).reason, 'PUBLISHER_ORIGIN_UNCHECKED')
        self.assertEqual(b.ready_body(raw, URL, 'Title', current, 'self').text, TEXT)

    def test_all_consumers_choose_same_body_and_full_hash(self):
        raw = {'content_capture': provider('short'), 'publisher_capture': capture()}
        expected = b.sha(TEXT); current = b.sha(embed.item_text('Title', TEXT))
        pcur = Cursor([(1, 'Title', URL, NOW, raw, current, 'self')])
        pil = pilot.load_items(pcur, '2026-11-01', '2026-11-01', new_only=True)[0]
        exc = extract.pending(Cursor([(1, 'Title', URL, raw, NOW, 1, 'self', current)]), '2026-11-01', '2026-11-01', 10)[0]
        emb = embed.pending(Cursor([(1, 'Title', URL, raw, NOW)]), 10)[0]
        for output in (pil, exc, emb):
            self.assertEqual(output['body_source_kind'], 'publisher_article')
            self.assertEqual(output['body_text_sha256'], expected)
            self.assertIn(TEXT.strip(), output['text'])
        self.assertIn('knowledge.item_processing', pcur.calls[0][0])
        self.assertTrue(pcur.calls[0][1][-2])

    def test_stale_publisher_never_enters_pilot_extract_or_history(self):
        raw = {'publisher_capture': capture()}
        self.assertEqual(pilot.load_items(Cursor([(1, 'Title', URL, NOW, raw, 'stale', 'self')]), '2026-11-01', '2026-11-01'), [])
        self.assertEqual(extract.pending(Cursor([(1, 'Title', URL, raw, NOW, 1, 'self', 'stale')]), '2026-11-01', '2026-11-01', 10), [])
        self.assertEqual(pilot.archive_hits(Cursor([(1, 'Title', URL, NOW, raw, 'stale', 'self')]), ['term'], '2026-11-02'), [])
        self.assertEqual(embed.similar_items(Cursor([(1, 'Title', URL, NOW, raw, .01, 'stale', 'self')]), [2], '2026-11-02'), [])

    def test_origin_unknown_is_not_relabelled_self(self):
        raw = {'content_capture': provider(TEXT)}
        self.assertEqual(extract.pending(Cursor([(1, 'Title', URL, raw, NOW, None, None, None)]), '2026-11-01', '2026-11-01', 10), [])
        reprint = extract.pending(Cursor([(2, 'Title', URL, raw, NOW, 1, 'same_text', None)]), '2026-11-01', '2026-11-01', 10)
        self.assertEqual(reprint[0]['origin_method'], 'same_text')

    def test_robots_forbidden_ratelimited_redirect_and_invalid_utf8_fail_closed(self):
        for code in ('HTTP_401', 'HTTP_403', 'HTTP_429', 'HTTP_500', 'HTTP_301'):
            calls = []
            def fetch(url, *_a, **_kw):
                calls.append(url); raise f.transport.FetchError(code)
            reason, cap = f.read_one(item(), f.Robots(fetch), fetch, lambda *_: TEXT, lambda *_: None)
            self.assertEqual(len(calls), 1); self.assertIsNone(cap)
            self.assertEqual(reason, 'robots_' + code.lower())
        robots = f.Robots(lambda *_a, **_kw: {'body': b'\xff'})
        self.assertEqual(robots.allowed(URL), (False, 'robots_utf8_invalid'))

    def test_pacing_includes_robots_and_article_and_counts_http(self):
        paced, calls = [], []
        def fetch(url, *_a, **_kw):
            calls.append(url)
            return {'status': 200, 'url': url, 'body': b'User-agent: *\nAllow: /' if url.endswith('/robots.txt') else b'page'}
        r = Cursor([(1, 2, URL, {'content_capture': provider('short')}, True, False, False, False)])
        result = f.run(Connection(r), '2026-11-01', '2026-11-01', fetch=fetch,
                       extract=lambda *_: TEXT, pace=paced.append)
        self.assertEqual(paced, ['publisher.example', 'publisher.example'])
        self.assertEqual((result['http_attempts'], result['robots_attempts']), (2, 1))
        self.assertEqual(result['written'], 0)
        self.assertIn("i.item_type='news'", r.calls[0][0])
        self.assertIn("s.name='Inoreader BOHUN'", r.calls[0][0])

    def test_utf8_bytes_are_not_silently_truncated(self):
        def fetch(url, *_a, **_kw): return {'status': 200, 'url': url, 'body': b'page'}
        for text, wanted in [('я' * 65536, 'ok'), ('я' * 65537, 'text_bytes_exceeded')]:
            with self.subTest(wanted=wanted):
                robots = f.Robots(lambda *_a, **_kw: {'body': b'User-agent: *\nAllow: /'})
                reason, cap = f.read_one(item(), robots, fetch, lambda *_: text, lambda *_: None)
                self.assertEqual(reason, wanted)
                if cap: self.assertEqual(cap['text'], text)

    def test_write_stale_receipt_no_overwrite_and_failure_rolls_back(self):
        source = item(); original = copy.deepcopy(source)
        for count, expected in [(1, 'WRITTEN'), (0, 'STALE_NOT_WRITTEN')]:
            conn = Connection(Cursor(rowcount=count))
            self.assertEqual(f.write_capture(conn, source, capture()), expected)
            self.assertEqual(conn.commits, 1)
            sql, args = conn.cur.calls[0]
            for required in ('i.source_id=%s', 'i.url IS NOT DISTINCT FROM %s', "? 'content_capture'",
                             "? 'publisher_capture'", 'knowledge.item_processing', 'knowledge.item_embedding'):
                self.assertIn(required, sql)
            self.assertEqual(args[3], URL)
            self.assertEqual(json.loads(args[-2]), source['raw']['content_capture'])
        self.assertEqual(source, original)
        conn = Connection(Cursor(error=True))
        with self.assertRaisesRegex(RuntimeError, '^PUBLISHER_WRITE_FAILED$'):
            f.write_capture(conn, source, capture())
        self.assertEqual((conn.commits, conn.rollbacks), (0, 1))

    def test_processed_embedded_or_existing_capture_is_not_repaired_or_replayed(self):
        for field in ('processed', 'embedded'):
            value = item(); value[field] = True
            conn = Connection(Cursor())
            self.assertEqual(f.write_capture(conn, value, capture()), 'WRITE_REQUIRES_NEW_UNPROCESSED_ITEM')
            self.assertEqual(conn.cur.calls, [])

        for cap in (capture(), {'text': 'broken'}):
            value = item({'publisher_capture': cap})
            conn = Connection(Cursor())
            self.assertEqual(f.write_capture(conn, value, capture()), 'WRITE_REQUIRES_NEW_UNPROCESSED_ITEM')
            self.assertEqual(conn.cur.calls, [])

    def test_extractor_version_and_no_silent_truncation(self):
        huge = 'я' * 65537
        module = types.SimpleNamespace(extract=lambda *_a, **_kw: huge)
        with patch.dict(sys.modules, {'trafilatura': module}), patch('importlib.metadata.version', return_value='2.3.1'):
            self.assertEqual(f.extract_text(b'page', URL), huge)
        with patch('importlib.metadata.version', return_value='different'):
            with self.assertRaisesRegex(ValueError, 'VERSION_MISMATCH'):
                f.extract_text(b'page', URL)

    def test_existing_or_invalid_capture_does_not_trigger_http_repair(self):
        for cap in (capture(), {'text': 'invalid'}):
            raw = {'content_capture': provider('short'), 'publisher_capture': cap}
            conn = Connection(Cursor([(1, 2, URL, raw, True, True, False, False)]))
            with patch.object(f.transport, 'fetch', side_effect=AssertionError('NO FETCH')) as fetch:
                stats = f.run(conn, '2026-11-01', '2026-11-01', write=True, fetch=fetch)
            self.assertEqual((stats['written'], stats['http_attempts']), (0, 0))
            fetch.assert_not_called()

    def test_partial_commit_has_explicit_stop_and_no_hidden_retry(self):
        rows = [(i, 2, URL + str(i), {'content_capture': provider('short')}, True, False, False, False)
                for i in (1, 2, 3)]
        class FailingCursor(Cursor):
            updates = 0
            def execute(self, sql, args=()):
                if sql.startswith('UPDATE'):
                    self.updates += 1
                    if self.updates == 2:
                        raise RuntimeError('SECRET')
                super().execute(sql, args)
        conn = Connection(FailingCursor(rows))
        def fetch(url, *_a, **_kw):
            return {'status': 200, 'url': url, 'body': b'User-agent: *\nAllow: /' if url.endswith('robots.txt') else b'page'}
        stats = f.run(conn, '2026-11-01', '2026-11-01', write=True, fetch=fetch,
                      extract=lambda *_: TEXT, pace=lambda *_: None)
        self.assertEqual((stats['written'], stats['stop'], stats['not_attempted']), (1, 'PUBLISHER_WRITE_FAILED', 1))
        self.assertEqual((conn.commits, conn.rollbacks, conn.cur.updates), (1, 1, 2))
        self.assertNotIn('SECRET', json.dumps(stats))

    def test_invalid_capture_or_raw_shape_fails_before_write(self):
        conn = Connection(Cursor())
        cap = capture(); cap['text'] = 'changed'
        self.assertEqual(f.write_capture(conn, item(), cap), 'CAPTURE_VALIDATION_FAILED')
        value = item(); value['raw'] = {'_raw_object': False}
        self.assertEqual(f.write_capture(conn, value, capture()), 'RAW_DATA_INVALID')
        self.assertEqual(conn.cur.calls, [])

    def test_limit_bound_precedes_database_access(self):
        for limit in (0, -1, 61, True):
            conn = Connection(Cursor())
            with self.assertRaises(ValueError):
                f.run(conn, '2026-11-01', '2026-11-01', limit=limit)
            self.assertEqual(conn.cur.calls, [])

    def test_deadline_at_last_item_is_not_reported_as_success(self):
        conn = Connection(Cursor([(1, 2, URL, {'content_capture': provider('short')}, True, False, False, False)]))
        clock = iter((0, 0, f.RUN_DEADLINE_SECONDS + 1))
        stats = f.run(conn, '2026-11-01', '2026-11-01', clock=lambda: next(clock),
                      fetch=lambda *_a, **_kw: self.fail('deadline must prevent HTTP'), pace=lambda *_: None)
        self.assertEqual(stats['stop'], 'RUN_DEADLINE')
        self.assertEqual(stats['http_attempts'], 0)

    def test_short_provider_index_input_is_preserved_without_claiming_full_body(self):
        raw = {'content_capture': provider('short')}
        result = embed.pending(Cursor([(1, 'Title', URL, raw, NOW)]), 10)[0]
        self.assertEqual(result['text'], embed.item_text('Title', 'short'))
        self.assertIsNone(result['body_source_kind'])
        self.assertIsNone(result['body_text_sha256'])
        self.assertIsNone(b.select_body(raw, URL).text)


if __name__ == '__main__': unittest.main()
