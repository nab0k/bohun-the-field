import csv
import datetime as dt
import gzip
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from collectors.source_harvest import backlog, canonical, cluster, discover, inputs, relevance, yield_metrics
from collectors.source_harvest.relevance_llm import BudgetLedger

FIXTURES = Path(__file__).with_name("fixtures")


class Response:
    def __init__(self, url, body, content_type="application/xml", status=200):
        self.url = url; self.content = body if isinstance(body, bytes) else body.encode(); self.text = self.content.decode()
        self.status_code = status; self.headers = {"content-type": content_type, "etag": '"fixture"'}; self.ok = status == 200
    def raise_for_status(self):
        if self.status_code >= 400: raise RuntimeError(str(self.status_code))


class FakeClient:
    def __init__(self, routes): self.routes = routes; self.request_count = 0
    def get(self, url, **kwargs):
        self.request_count += 1
        if url not in self.routes: return Response(url, "missing", "text/plain", 404)
        value = self.routes[url]
        return value if isinstance(value, Response) else Response(url, value)


class CanonicalTests(unittest.TestCase):
    def test_tracking_www_fragment_and_https(self):
        self.assertEqual(canonical.canonical_url("http://WWW.Example.COM/news/?utm_source=x&b=2#frag"), "https://example.com/news?b=2")

    def test_provenance_dedup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "data/sources").mkdir(parents=True); (root / "docs/sources").mkdir(parents=True)
            (root / "data/sources/registry.csv").write_text("source_id,homepage_url,feed_url\n", encoding="utf-8")
            (root / "docs/sources/global-source-candidates-20261009.csv").write_text("name,url\nOne,https://www.example.com/\n", encoding="utf-8")
            (root / "docs/sources/notion-top100-reconciliation-20261009.csv").write_text("name,homepage_url\nOne again,http://example.com/?utm_source=x\n", encoding="utf-8")
            rows, _, _ = inputs.collect(root, max_candidates=10)
            self.assertEqual(len(rows), 1); self.assertEqual(rows[0]["provenance"], "I5")


class DiscoveryTests(unittest.TestCase):
    def test_html_autodiscovery_and_rss_validation(self):
        html = (FIXTURES / "wordpress.html").read_text()
        rss = (FIXTURES / "rss.xml").read_bytes()
        candidate = {"candidate_id": "c", "site_id": "s", "site_url": "https://example.com", "feed_url": "", "language": ""}
        client = FakeClient({"https://example.com": Response("https://example.com", html, "text/html"), "https://example.com/feed": Response("https://example.com/feed", rss)})
        row, miss = discover.discover_one(candidate, client)
        self.assertIsNone(miss); self.assertEqual(row["format"], "rss"); self.assertEqual(row["discovery_method"], "html-alternate")

    def test_arc_common_path_and_no_feed(self):
        html = (FIXTURES / "arc.html").read_text()
        candidate = {"candidate_id": "c", "site_id": "s", "site_url": "https://arc.example", "feed_url": "", "language": ""}
        arc = "https://arc.example/arc/outboundfeeds/rss/"
        client = FakeClient({"https://arc.example": Response("https://arc.example", html, "text/html"), arc: Response(arc, (FIXTURES / "atom.xml").read_bytes())})
        row, _ = discover.discover_one(candidate, client)
        self.assertEqual(row["discovery_method"], "common-path")
        missing, miss = discover.discover_one({**candidate, "site_url": "https://none.example"}, FakeClient({}))
        self.assertIsNone(missing); self.assertEqual(miss["has_news_section"], "false")

    def test_news_sitemap(self):
        response = Response("https://news.example/sitemap_news.xml", (FIXTURES / "news-sitemap.xml").read_bytes())
        row = discover.validate_feed(response, {"site_id": "s", "language": ""}, "sitemap")
        self.assertEqual(row["format"], "news-sitemap"); self.assertEqual(row["entries_count"], 1)


class BacklogTests(unittest.TestCase):
    def test_metadata_only_and_undated(self):
        rss = (FIXTURES / "rss.xml").read_bytes(); url = "https://example.com/feed"
        items, coverage = backlog.fetch_feed({"feed_id": "f", "feed_url": url, "format": "rss"}, FakeClient({url: Response(url, rss)}), days_back=60, now=dt.datetime(2026, 10, 14, tzinfo=dt.timezone.utc))
        self.assertEqual(len(items), 2); self.assertEqual(items[1]["published_at"], "")
        self.assertNotIn("summary", items[0]); self.assertEqual(json.loads(items[0]["summary_links"]), ["https://supplier.example/news"])
        self.assertGreater(coverage["backlog_coverage_days"], 0)

    def test_pagination_stops_on_old_page(self):
        base = "https://example.com/feed"; page2 = base + "?paged=2"
        current = (FIXTURES / "rss.xml").read_bytes()
        old = current.replace(b"13 Oct 2026", b"13 Aug 2026")
        client = FakeClient({base: Response(base, current), page2: Response(page2, old)})
        backlog.fetch_feed({"feed_id": "f", "feed_url": base, "format": "rss"}, client, days_back=30, now=dt.datetime(2026, 10, 14, tzinfo=dt.timezone.utc))
        self.assertLessEqual(client.request_count, 2)


class AnalysisTests(unittest.TestCase):
    def test_clusters_copy_reprint_and_distinct(self):
        base = {"fetched_at": "2026-10-14T00:00:00+00:00", "published_at": "2026-10-13T00:00:00+00:00"}
        items = [
            {**base, "item_id": "1", "feed_id": "a", "canonical_url": "https://x/a", "title": "Germany awards major defence contract - Reuters"},
            {**base, "item_id": "2", "feed_id": "b", "canonical_url": "https://x/a", "title": "Germany awards major defence contract"},
            {**base, "item_id": "3", "feed_id": "c", "canonical_url": "https://y/a", "title": "Germany awards major defence contract to Rheinmetall"},
            {**base, "item_id": "4", "feed_id": "d", "canonical_url": "https://z/b", "title": "Germany wins football match"},
        ]
        clusters, members = cluster.cluster_items(items)
        self.assertEqual(len(clusters), 2); self.assertIn("copy", {row["relation"] for row in members}); self.assertIn("reprint", {row["relation"] for row in members})

    def test_relevance_positive_negative_and_rule_ids(self):
        positives = ["Germany awards defence contract", "Ukraine startup raises funding round", "NATO announces flight test certification"]
        negatives = ["Football match report", "Celebrity movie review", "Frontline update: killed in battle"]
        for title in positives:
            row = relevance.classify(title); self.assertEqual(row["relevant"], "yes"); self.assertTrue(row["rule_ids"])
        for title in negatives: self.assertEqual(relevance.classify(title)["relevant"], "no")

    def test_three_terms_for_every_signal(self):
        rules = relevance.load_rules()
        for signal, terms in rules["signals"].items():
            self.assertGreaterEqual(len(terms), 3)
            for term in terms[:3]:
                row = relevance.classify("Industry update: " + term, rules)
                self.assertEqual(row["relevant"], "yes", (signal, term, row))
                self.assertTrue(row["rule_ids"])
        for signal in rules["signals"]:
            for title in ("Football match report", "Celebrity movie review", "Sports results"):
                self.assertEqual(relevance.classify(title, rules)["relevant"], "no", signal)

    def test_unique_yield_known_answer(self):
        clusters = [{"cluster_id": "x", "relevant": "yes", "first_seen_feed": "a", "signal": "funding", "region": "UA"}, {"cluster_id": "y", "relevant": "yes", "first_seen_feed": "b", "signal": "funding", "region": "UA"}]
        members = [{"cluster_id": "x", "feed_id": "a", "relation": "independent"}, {"cluster_id": "x", "feed_id": "b", "relation": "reprint"}, {"cluster_id": "y", "feed_id": "b", "relation": "independent"}]
        items = [{"feed_id": "a"}] * 20 + [{"feed_id": "b"}] * 20
        rows = yield_metrics.calculate(items, clusters, members, [{"feed_id": "a", "backlog_coverage_days": 30}, {"feed_id": "b", "backlog_coverage_days": 30}], 7)
        self.assertEqual({row["feed_id"]: row["unique_relevant_per_week"] for row in rows}, {"a": 1.0, "b": 1.0})

    def test_budget_stops_before_cap(self):
        ledger = BudgetLedger(.001)
        with self.assertRaisesRegex(RuntimeError, "GEMINI_BUDGET_BLOCKED"): ledger.reserve(1_000_000, 10_000)
        self.assertEqual(ledger.spent_usd, 0)


class SafetyTests(unittest.TestCase):
    def test_inoreader_429_continues(self):
        fake = types.ModuleType("sync")
        fake.access_token = lambda: "hidden"
        fake.api = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("HTTP request failed: stage=inoreader_api, status=429"))
        # capture stdout so this fixture notice never shows up as a real GitHub annotation
        with mock.patch.dict(sys.modules, {"sync": fake}), mock.patch("sys.stdout", io.StringIO()) as out:
            rows, meta = inputs._inoreader_candidates(20)
        self.assertIn("INOREADER_QUOTA_BLOCKED", out.getvalue())
        self.assertEqual(rows, []); self.assertTrue(meta["blocked"])

    def test_rotated_refresh_token_notice_hides_values(self):
        from collectors.inoreader import sync
        response = {"access_token": "access-secret", "refresh_token": "new-refresh-secret"}
        env = {"INOREADER_CLIENT_ID": "id-secret", "INOREADER_CLIENT_SECRET": "client-secret", "INOREADER_REFRESH_TOKEN": "old-refresh-secret"}
        stream = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(sync, "request_json", return_value=response), mock.patch("sys.stderr", stream):
            self.assertEqual(sync.access_token(), "access-secret")
        notice = stream.getvalue()
        self.assertIn("INOREADER_REFRESH_TOKEN_ROTATED", notice)
        for secret in list(response.values()) + list(env.values()):
            self.assertNotIn(secret, notice)

    def test_no_forbidden_inoreader_writes_or_full_text_fields(self):
        code = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "collectors/source_harvest").glob("*.py"))
        for forbidden in ("subscription/edit", "subscription/quickadd", "edit-tag", "mark-all-as-read"):
            self.assertNotIn(forbidden, code)
        self.assertNotIn("content_text", backlog.ITEM_FIELDS); self.assertNotIn("article_body", backlog.ITEM_FIELDS)


class FollowUpTests(unittest.TestCase):
    """Fixes after the 2026-10-14 small run."""

    def test_zero_item_feed_keeps_a_row(self):
        feeds = [{"feed_id": "a", "site_id": "s1", "feed_url": "https://a.example/feed"},
                 {"feed_id": "z", "site_id": "s2", "feed_url": "https://z.example/feed"}]
        rows = yield_metrics.calculate([{"feed_id": "a"}] * 3, [], [], [{"feed_id": "a", "backlog_coverage_days": 3}], 30, feeds=feeds)
        by_id = {row["feed_id"]: row for row in rows}
        self.assertIn("z", by_id)
        self.assertEqual(by_id["z"]["status"], "no_items")
        self.assertEqual(by_id["z"]["history_bucket"], "none")
        self.assertEqual(by_id["a"]["feed_url"], "https://a.example/feed")

    def test_history_buckets(self):
        self.assertEqual([yield_metrics.history_bucket(d) for d in (0, 3, 7, 13.9, 14, 29, 30, 60)],
                         ["none", "<7d", "7-13d", "7-13d", "14-29d", "14-29d", ">=30d", ">=30d"])

    def test_recompute_coverage_after_inoreader_history(self):
        now = dt.datetime(2026, 10, 14, tzinfo=dt.timezone.utc)
        items = [{"feed_id": "a", "published_at": (now - dt.timedelta(days=d)).isoformat()} for d in (1, 2, 25)]
        out = backlog.recompute_coverage([{"feed_id": "a", "backlog_coverage_days": 2, "items": 2}, {"feed_id": "b", "backlog_coverage_days": 5}], items, {"a"}, now=now)
        self.assertEqual(out[0]["backlog_coverage_days"], 25.0)
        self.assertEqual(out[0]["history_source"], "feed+inoreader")
        self.assertEqual(out[1]["backlog_coverage_days"], 5)

    def test_inoreader_history_uses_subscription_stream_id_and_survives_404(self):
        calls = []
        def api(path, token, params=None):
            calls.append(path)
            if "bad" in path:
                raise RuntimeError("HTTP request failed: stage=inoreader_api, status=404")
            return {"items": [{"id": "i1", "title": "Contract awarded", "canonical": [{"href": "https://a.example/x"}], "published": 1791900000}]}
        fake = types.ModuleType("sync"); fake.access_token = lambda: "t"; fake.api = api
        feeds = [{"feed_id": "f1", "site_id": "s1", "feed_url": "https://a.example/feed"},
                 {"feed_id": "f2", "site_id": "s2", "feed_url": "https://bad.example/feed"}]
        candidates = [{"site_id": "s1", "provenance": "I1|I2", "inoreader_stream_id": "feed/http://www.a.example/feed/"},
                      {"site_id": "s2", "provenance": "I1|I2", "inoreader_stream_id": "feed/https://bad.example/feed"}]
        coverage = [{"feed_id": "f1", "backlog_coverage_days": 2}, {"feed_id": "f2", "backlog_coverage_days": 1}]
        with mock.patch.dict(sys.modules, {"sync": fake}):
            added, used, blocked = backlog.inoreader_history(feeds, coverage, candidates, [], call_budget=10, days_back=30,
                                                             now=dt.datetime(2026, 10, 14, tzinfo=dt.timezone.utc))
        self.assertFalse(blocked); self.assertEqual(used, 2); self.assertEqual(len(added), 1)
        self.assertTrue(any("http%3A//www.a.example/feed/" in c or "http://www.a.example/feed/" in c for c in calls))

    def test_discover_deadline_records_not_attempted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidates.csv"
            with path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["candidate_id", "site_id", "site_url", "feed_url"])
                writer.writeheader(); writer.writerow({"candidate_id": "c1", "site_id": "s1", "site_url": "https://a.example", "feed_url": ""})
            feeds, pending, _ = discover.run(path, tmp, client=FakeClient({}), deadline_seconds=-1)
        self.assertEqual(feeds, [])
        self.assertEqual(pending[0]["method_tried"], "deadline_not_attempted")

    def test_comment_feeds_rejected(self):
        self.assertTrue(discover.is_comment_feed("https://site.example/comments/feed/"))
        self.assertTrue(discover.is_comment_feed("https://site.example/feed", "Comments for EDR Magazine"))
        self.assertFalse(discover.is_comment_feed("https://site.example/feed/", "EDR Magazine"))

    def test_backlog_sends_no_conditional_headers(self):
        seen = []
        class Client(FakeClient):
            def get(self, url, **kwargs):
                seen.append(kwargs); return super().get(url)
        rss = (FIXTURES / "rss.xml").read_text()
        feed = {"feed_id": "f", "feed_url": "https://a.example/feed", "format": "rss", "etag": '"x"', "last_modified": "Mon"}
        backlog.fetch_feed(feed, Client({"https://a.example/feed": rss}), days_back=3650, max_pages=1)
        self.assertTrue(seen); self.assertFalse(any(k.get("etag") or k.get("last_modified") for k in seen))


if __name__ == "__main__": unittest.main()
