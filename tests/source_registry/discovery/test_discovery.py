"""Offline behavioral tests. Network, DNS, credentials and real DB are forbidden."""
import contextlib
import copy
import datetime as dt
import hashlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import urllib.request
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve()
FIELD = HERE.parents[3]
PACKAGE = FIELD / "collectors/source_registry/discovery"
sys.path.insert(0, str(FIELD))


def blocked(*args, **kwargs):
    raise AssertionError("REAL_EXTERNAL_IO_FORBIDDEN")


with patch.object(socket, "getaddrinfo", side_effect=blocked), \
     patch.object(urllib.request, "urlopen", side_effect=blocked):
    from collectors.source_registry.discovery.policy import Problem, GROUPS, assess, digest, epoch, identity_url, load_policy, parse_json, stamp
    from collectors.source_registry.discovery.store import Store
    from collectors.source_registry.discovery.radars import API, Budget, HTTP, Response, artifact_page, feed_data, publisher_observation, radar_pages, resolve_public, xml_root
    from collectors.source_registry.discovery.harvest import checked_feed, discover
    from collectors.source_registry.discovery.collectors import Bridge, load_manager
    from collectors.source_registry.discovery.discovery import Worker, main, oauth_token, serve, validate_config
    from collectors.source_registry.discovery.health import evaluate, optional_connect, read_delivery
    from collectors.inoreader import inoreader_source_manager as manager

POLICY, POLICY_HASH = load_policy(PACKAGE / "policy.proposed.json")
FIXTURE = json.loads((HERE.parent / "fixtures/synthetic.json").read_text())


class Clock:
    def __init__(self):
        self.value = epoch(FIXTURE["date"])
        self.mono = 100.0

    def now(self):
        return self.value

    def monotonic(self):
        return self.mono

    def advance(self, n=1):
        self.value += n
        self.mono += n


def rss(entries=None, title=None, language="en"):
    entries = FIXTURE["entries"] if entries is None else entries
    items = []
    for e in entries:
        items.append("<item><guid>" + escape(str(e.get("id", e["url"]))) + "</guid><title>" +
                     escape(e["title"]) + "</title><link>" + escape(e["url"]) + "</link><pubDate>" +
                     escape(e.get("published", "")) + "</pubDate></item>")
    return ("<rss><channel><title>" + escape(title or FIXTURE["title"]) + "</title><language>" + language +
            "</language>" + "".join(items) + "</channel></rss>").encode()


class Crash(BaseException):
    pass


class Server:
    def __init__(self):
        self.subs = {"feed/https://existing" + str(i) + ".example/feed":
                     {"id": "feed/https://existing" + str(i) + ".example/feed", "categories": [{"label": "BOHUN_MEDIA"}]}
                     for i in range(68)}
        self.posts = []
        self.mode = "ok"
        self.radar_errors = {}
        self.stream_pages = None
        self.requests = []
        self.receipts = {}

    def answer(self, url, method, body):
        path = urlsplit(url).path
        self.requests.append((method, url))
        if url.startswith(API):
            if "subscription/list" in path:
                payload = {"subscriptions": copy.deepcopy(list(self.subs.values()))}
                return 200, json.dumps(payload).encode(), {}
            if "subscription/quickadd" in path:
                values = {k: v[0] for k, v in parse_qs(body.decode()).items()}
                if self.mode == "crash_before_send":
                    raise Crash()
                self.posts.append(("quickadd", values))
                stream = "feed/" + values["quickadd"]
                self.subs[stream] = {"id": stream, "categories": []}
                if self.mode == "timeout_after_add":
                    raise Problem("TRANSPORT_FAILURE")
                return 200, json.dumps({"streamId": stream}).encode(), {}
            if "subscription/edit" in path:
                values = {k: v[0] for k, v in parse_qs(body.decode()).items()}
                self.posts.append(("edit", values))
                self.subs[values["s"]]["categories"] = [{"id": values["a"]}]
                if self.mode == "crash_after_folder":
                    raise Crash()
                return 200, b"OK", {}
            if "stream/contents/" in path:
                if self.radar_errors.get("inoreader"):
                    return self.radar_errors["inoreader"], b"{}", {}
                if self.stream_pages is not None:
                    page = self.stream_pages.pop(0)
                    if isinstance(page, Exception):
                        raise page
                    return 200, json.dumps(page).encode(), {}
                return 200, json.dumps({"items": [{"id": "synthetic-1", "title": "Defence contract award",
                    "alternate": [{"href": FIXTURE["entries"][0]["url"]}],
                    "origin": {"streamId": "feed/" + FIXTURE["feed"]}}]}).encode(), {}
        if url == FIXTURE["radars"]["google_alerts"]:
            item = dict(FIXTURE["entries"][0], url="https://www.google.com/url?url=" + FIXTURE["entries"][0]["url"])
            return 200, rss([item], "Google Alerts"), {}
        if url == FIXTURE["radars"]["talkwalker"]:
            return 200, rss(FIXTURE["entries"][:1], "Talkwalker"), {}
        if url == FIXTURE["feed"] or url.startswith("https://publisher.example/feed/"):
            return 200, rss(), {"content-type": "application/rss+xml"}
        if url == FIXTURE["publisher"]:
            return 200, b'<html lang="en"><link rel="alternate" type="application/rss+xml" href="/feed"></html>', {"content-type": "text/html"}
        return 404, b"missing", {}

    def delivery(self, profile):
        connection = profile["connection"]
        return {"read_only": True, "origin_stream_id": "feed/" + profile["feed_url"],
                "folder": connection["folder"], "records": copy.deepcopy(self.receipts.get(profile["feed_url"], []))}


class FakeHTTP:
    def __init__(self, server, budget, reserve):
        self.server, self.budget, self.reserve = server, budget, reserve

    def request(self, url, *, method="GET", body=None, api=False, **kwargs):
        self.budget.request()
        if api:
            self.reserve(uuid.uuid4().hex)
        status, raw, headers = self.server.answer(url, method, body)
        self.budget.add_bytes(len(raw))
        self.budget.trace.append({"method": method, "status": status, "url_hash": digest(url), "bytes": len(raw)})
        return Response(status, raw, url, headers)


class Offline(unittest.TestCase):
    def setUp(self):
        for owner, name in ((socket, "getaddrinfo"), (socket, "create_connection"), (socket.socket, "connect"),
                            (urllib.request, "urlopen"), (subprocess, "Popen")):
            guard = patch.object(owner, name, side_effect=blocked)
            guard.start()
            self.addCleanup(guard.stop)
        guard = patch.dict(sys.modules, {"psycopg": SimpleNamespace(connect=blocked)})
        guard.start()
        self.addCleanup(guard.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.clock = Clock()

    def state(self, name="worker", bootstrap=True):
        store = Store(self.root / name, "test-" + name, bootstrap=bootstrap)
        self.addCleanup(store.close)
        return store

    def feed(self, url=None):
        url = url or FIXTURE["feed"]
        items = [dict(e,url="https://"+urlsplit(url).netloc+"/news/"+str(i))
                 for i,e in enumerate(FIXTURE["entries"])]
        feed = checked_feed(Response(200, rss(items), url, {}), url, self.clock.now())
        feed["assessment"] = assess(feed, POLICY, self.clock.now())
        feed["policy_sha256"] = POLICY_HASH
        return feed

    def profile(self, store, url=None):
        feed = self.feed(url)
        obs = {"item_id": digest(feed["feed_url"]), "feed_url": feed["feed_url"], "title": "synthetic"}
        store.ingest("tool", "fixture", [obs], {}, self.clock.now())
        store.validate(digest(feed["feed_url"]), [feed], self.clock.now())
        return next(p for p in store.profiles() if p["feed_url"] == feed["feed_url"])

    def worker(self, store, server, **changes):
        config = {"version": 1, "live_inputs": True, "writer_enabled": True, "collector_ready": True,
                  "pilot_proofs": {"single": True, "batch3": True}, "providers": [
                      {"id": "ino", "type": "inoreader", "query_id": "q", "enabled": True,
                       "stream": FIXTURE["radars"]["inoreader_stream"]},
                      {"id": "google", "type": "google_alerts", "query_id": "q", "enabled": True,
                       "url": FIXTURE["radars"]["google_alerts"]},
                      {"id": "talk", "type": "talkwalker", "query_id": "q", "enabled": True,
                       "url": FIXTURE["radars"]["talkwalker"]}], "artifacts": []}
        config.update(changes)
        return Worker(store, POLICY, POLICY_HASH, config, clock=self.clock.now, monotonic=self.clock.monotonic,
                      http_factory=lambda b, r: FakeHTTP(server, b, r), token_provider=lambda: "OFFLINE_SECRET",
                      manager=manager, delivery_reader=server.delivery)

    def bridge(self, store, server):
        run = store.start_run(self.clock.now())
        budget = Budget(POLICY["limits"], monotonic=self.clock.monotonic)
        return Bridge(manager, FakeHTTP(server, budget, lambda _: None), lambda: "OFFLINE_SECRET",
                      store, POLICY, run, self.clock.now, live_enabled=True,
                      pilot_proofs={"single": True, "batch3": True})


class Contracts(Offline):
    def test_json_duplicate_and_nonfinite_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'not json'):
            with self.subTest(raw=raw), self.assertRaises(Problem):
                parse_json(raw)

    def test_urls_preserve_language_path_query_and_slash(self):
        self.assertEqual(identity_url("https://EXAMPLE.org/uk/feed/?format=rss#x"), "https://example.org/uk/feed/?format=rss")
        self.assertNotEqual(identity_url("https://example.org/feed/"), identity_url("https://example.org/feed"))
        self.assertNotEqual(identity_url("https://example.org/en/feed"), identity_url("https://example.org/uk/feed"))

    def test_unsafe_or_capability_urls_rejected(self):
        for url in ("http://example.org", "https://u:p@example.org/", "https://127.0.0.1/", "https://[::1]/",
                    "https://example.org/?api_key=PRIVATE", "https://example.org:444/", "https://localhost/",
                    "https://a.internal/", "https://example.org/\n", "https://example.org\\@localhost/"):
            with self.subTest(url=url), self.assertRaises(Problem):
                identity_url(url)

    def test_good_sample_has_explainable_admission_and_no_trust_claim(self):
        result = self.feed()["assessment"]
        self.assertEqual(result["decision"], "AUTO_ADMIT")
        self.assertEqual(result["group"], GROUPS["MEDIA"])
        self.assertEqual(result["relevant_count"], 15)
        self.assertEqual(result["trust"], "NOT_ESTABLISHED")

    def test_small_old_unknown_language_and_undated_are_review(self):
        original = self.feed()
        for change in ({"entries": original["entries"][:3]}, {"language": "zz"},
                       {"entries": [dict(e, published_epoch=None) for e in original["entries"]]},
                       {"entries": [dict(e, published_epoch=0) for e in original["entries"]]}):
            self.assertEqual(assess({**original, **change}, POLICY, self.clock.now())["decision"], "REVIEW")

    def test_general_business_signal_without_defence_domain_is_review(self):
        feed = self.feed()
        feed["entries"] = [dict(e, title="Startup raises new funding round") for e in feed["entries"]]
        result = assess(feed, POLICY, self.clock.now())
        self.assertEqual(result["decision"], "REVIEW")
        self.assertEqual(result["relevant_count"], 0)

    def test_ambiguous_route_no_silent_default(self):
        feed = self.feed()
        feed["title"] = "Research funding calls"
        result = assess(feed, POLICY, self.clock.now())
        self.assertIsNone(result["group"])
        self.assertEqual(result["decision"], "REVIEW")

    def test_government_and_opportunities_routes(self):
        self.assertEqual(assess(self.feed("https://agency.gov/feed"), POLICY, self.clock.now())["group"], GROUPS["GOV"])
        feed = self.feed()
        feed["title"] = "DARPA Opportunities"
        self.assertEqual(assess(feed, POLICY, self.clock.now())["group"], GROUPS["OPPORTUNITIES"])

    def test_xml_dtd_utf16_and_complexity_rejected(self):
        for raw in (b'<!DOCTYPE rss [<!ENTITY x "x">]><rss/>', '<!DOCTYPE rss><rss/>'.encode("utf-16"),
                    ("<a>" * 66 + "</a>" * 66).encode()):
            with self.assertRaises(Problem):
                xml_root(raw)

    def test_rss_atom_language_and_relative_links(self):
        atom = b'<feed xmlns="http://www.w3.org/2005/Atom" xml:lang="en"><title>Defence News</title><entry><id>1</id><title>Contract</title><link href="/story"/><updated>2026-10-24T00:00:00Z</updated></entry></feed>'
        parsed = feed_data(atom, FIXTURE["feed"])
        self.assertEqual(parsed["entries"][0]["url"], "https://publisher.example/story")
        self.assertEqual(parsed["language"], "en")

    def test_protected_and_comment_feeds_not_auto_admitted(self):
        feed = checked_feed(Response(200, rss([dict(e, title="Захищено: " + e["title"]) for e in FIXTURE["entries"]]),
                                     FIXTURE["feed"], {}), FIXTURE["feed"], self.clock.now())
        self.assertEqual(feed["access"], "RESTRICTED")
        self.assertEqual(assess(feed, POLICY, self.clock.now())["decision"], "REVIEW")
        self.assertEqual(self.feed("https://publisher.example/comments/feed")["assessment"]["decision"], "REVIEW")

    def test_wrapper_extracts_publisher_and_does_not_promote_aggregator(self):
        obs = publisher_observation({"item_id": "1", "url": "https://www.google.com/url?url=https%3A%2F%2Fpublisher.example%2Fa"}, "google_alerts")
        self.assertEqual(obs["publisher_url"], FIXTURE["publisher"])
        obs = publisher_observation({"item_id": "2", "url": "https://news.google.com/rss/articles/opaque"}, "google_alerts")
        self.assertNotIn("publisher_url", obs)
        self.assertEqual(obs["error"], "PUBLISHER_REVIEW")

    def test_unknown_api_or_sitemap_routes_are_review(self):
        class Source:
            def __init__(self, body, ctype): self.body, self.ctype = body, ctype
            def request(self, url, **kw): return Response(200, self.body, url, {"content-type": self.ctype})
        for source in (Source(b'{"data":[]}', "application/json"), Source(b'<urlset/>', "application/xml")):
            with self.assertRaisesRegex(Problem, "ADAPTER_REVIEW"):
                discover(FIXTURE["publisher"], source, self.clock.now())

    def test_config_and_policy_limits_are_strict(self):
        with self.assertRaises(Problem):
            validate_config({"version":1,"providers":[{"id":"a","query_id":"q","enabled":True,"type":"unknown"}]})
        raw = json.loads((PACKAGE / "policy.proposed.json").read_text())
        raw["limits"]["max_add_day"] = 999
        path = self.root / "policy.json"
        path.write_text(json.dumps(raw))
        with self.assertRaisesRegex(Problem, "POLICY_LIMIT_VALUE"):
            load_policy(path)


class StateTests(Offline):
    def test_exclusive_writer_lock(self):
        self.state()
        with self.assertRaises(OSError):
            Store(self.root / "worker", "test-worker")

    def test_missing_database_or_corruption_never_silently_bootstraps(self):
        store = self.state()
        store.close()
        (self.root / "worker/state.sqlite").unlink()
        with self.assertRaises(Problem): Store(self.root / "worker", "test-worker")
        with self.assertRaises(Problem): Store(self.root / "worker", "test-worker", bootstrap=True)

    def test_new_state_path_cannot_reset_same_deployment_budget(self):
        self.state()
        with self.assertRaisesRegex(Problem, "BOOTSTRAP_ALREADY_EXISTS"):
            Store(self.root / "other", "test-worker", bootstrap=True)

    def test_ingest_and_cursor_are_atomic_and_repeat_is_idempotent(self):
        store = self.state()
        good = {"item_id": "one", "publisher_url": FIXTURE["publisher"]}
        bad = {"item_id": "two", "publisher_url": "http://localhost/"}
        with self.assertRaises(Problem): store.ingest("radar", "q", [good, bad], {"page": 2}, self.clock.now())
        self.assertEqual(store.summary()["observations"], 0)
        self.assertEqual(store.cursor("radar"), {})
        self.assertEqual(store.ingest("radar", "q", [good], {"page": 1}, self.clock.now()), 1)
        self.assertEqual(store.ingest("radar", "q", [good], {"page": 1}, self.clock.now()), 0)
        self.assertEqual(store.summary()["candidates"], 1)

    def test_artifact_cursor_imports_later_rows_not_just_first_page(self):
        path = self.root / "candidates.csv"
        path.write_text("website,name_hint\nhttps://one.example/,one\nhttps://two.example/,two\n")
        config = {"path": str(path)}
        one, cursor = artifact_page(config, {}, 1)
        two, cursor = artifact_page(config, cursor, 1)
        self.assertNotEqual(one[0]["publisher_url"], two[0]["publisher_url"])
        self.assertEqual(cursor["coverage"], "IMPORTED")
        self.assertEqual(artifact_page(config, cursor, 1)[0], [])

    def test_retry_is_due_after_backoff(self):
        store = self.state()
        store.ingest("radar", "q", [{"item_id":"one","publisher_url":FIXTURE["publisher"]}], {}, self.clock.now())
        cid = store.due(self.clock.now(), 1)[0]["id"]
        store.fail(cid, "ROBOTS_UNAVAILABLE", self.clock.now(), review=True)
        self.assertEqual(store.due(self.clock.now() + 100, 10), [])
        self.assertEqual(len(store.due(self.clock.now() + 86401, 10)), 1)

    def test_language_conflict_does_not_overwrite_existing_profile(self):
        store = self.state()
        profile = self.profile(store)
        changed = self.feed()
        changed["language"] = "uk"
        store.validate(profile["candidate_id"], [changed], self.clock.now())
        self.assertEqual(len(store.profiles()), 1)
        self.assertEqual(store.profiles()[0]["language"], "en")
        self.assertEqual(store.summary()["review_events"], 1)

    def test_reservation_survives_restart_and_resets_only_utc_day(self):
        store = self.state()
        now = self.clock.now()
        store.reserve("one", "run1", "quickadd", now, day_limit=1, cycle_limit=1)
        store.close()
        store = self.state(bootstrap=False)
        with self.assertRaisesRegex(Problem, "QUOTA_BLOCKED"):
            store.reserve("two", "run2", "quickadd", now, day_limit=1, cycle_limit=1)
        with self.assertRaisesRegex(Problem, "ATTEMPT_ALREADY_RESERVED"):
            store.reserve("one", "run2", "quickadd", now + 86400, day_limit=1, cycle_limit=1)
        store.reserve("two", "run2", "quickadd", now + 86400, day_limit=1, cycle_limit=1)


class WireResponse:
    def __init__(self, status=200, raw=b"ok", headers=None):
        self.status, self.raw, self.headers = status, raw, headers or {}
    def getheaders(self): return list(self.headers.items())
    def read1(self, n):
        block, self.raw = self.raw[:n], self.raw[n:]
        return block


class WireConnection:
    def __init__(self, response):
        self.response, self.sock = response, SimpleNamespace(settimeout=lambda n: None)
    def request(self, *args, **kwargs): pass
    def getresponse(self): return self.response
    def close(self): pass


class TransportTests(Offline):
    def client(self, responses, *, limit_changes=None):
        limits = {**POLICY["limits"], **(limit_changes or {})}
        budget = Budget(limits, monotonic=self.clock.monotonic)
        calls = []
        def factory(host, address, family, timeout):
            calls.append((host, address))
            return WireConnection(responses.pop(0))
        client = HTTP(budget, enabled=True, resolver=lambda *a: ("8.8.8.8", socket.AF_INET),
                      connection_factory=factory, sleep=self.clock.advance, reserve_api=lambda _: None)
        return client, calls

    def test_pinned_factory_receives_verified_ip_and_original_hostname(self):
        client, calls = self.client([WireResponse()])
        self.assertEqual(client.request("https://public.example/feed").body, b"ok")
        self.assertEqual(calls, [("public.example", "8.8.8.8")])

    def test_private_redirect_stopped_before_second_connection(self):
        client, calls = self.client([WireResponse(302, b"", {"location":"https://127.0.0.1/feed"})])
        with self.assertRaises(Problem): client.request("https://public.example/feed")
        self.assertEqual(len(calls), 1)

    def test_auth_not_forwarded_cross_origin_and_post_not_retried(self):
        for method in ("GET", "POST"):
            client, calls = self.client([WireResponse(302, b"", {"location":"https://other.example/feed"})])
            with self.assertRaises(Problem):
                client.request("https://public.example/feed", method=method, headers={"Authorization":"secret"})
            self.assertEqual(len(calls), 1)

    def test_robots_html_or_403_blocks_source_fetch(self):
        for response in (WireResponse(200,b"<html>blocked</html>",{"content-type":"text/html"}), WireResponse(403)):
            client, calls = self.client([response])
            with self.assertRaises(Problem): client.request("https://public.example/feed", robots=True)
            self.assertEqual(len(calls), 1)

    def test_robots_404_allows_but_disallow_stops(self):
        client, calls = self.client([WireResponse(404), WireResponse(200)])
        client.request("https://public.example/feed", robots=True)
        self.assertEqual(len(calls), 2)
        client, calls = self.client([WireResponse(200,b"User-agent: *\nDisallow: /feed")])
        with self.assertRaisesRegex(Problem, "ROBOTS_DISALLOWED"):
            client.request("https://public.example/feed", robots=True)

    def test_size_compression_request_and_time_caps(self):
        cases = ([WireResponse(200,b"123456789")], [WireResponse(200,b"",{"Content-Length":"99999999"})],
                 [WireResponse(200,b"",{"Content-Encoding":"gzip"})])
        for responses in cases:
            client, _ = self.client(responses)
            with self.assertRaises(Problem): client.request("https://public.example/feed", limit=8)
        client, _ = self.client([WireResponse(), WireResponse()], limit_changes={"max_requests":1})
        client.request("https://public.example/feed")
        with self.assertRaisesRegex(Problem, "HTTP_REQUEST_CAP"): client.request("https://public.example/feed")
        budget = Budget({**POLICY["limits"], "max_seconds":1}, monotonic=self.clock.monotonic)
        self.clock.advance(2)
        with self.assertRaisesRegex(Problem, "CYCLE_TIME_CAP"): budget.request()

    def test_mixed_public_private_dns_rejected(self):
        records = [(socket.AF_INET,socket.SOCK_STREAM,6,"",("8.8.8.8",443)),
                   (socket.AF_INET,socket.SOCK_STREAM,6,"",("127.0.0.1",443))]
        with patch.object(socket,"getaddrinfo",return_value=records):
            with self.assertRaisesRegex(Problem,"DNS_PRIVATE"): resolve_public("public.example",1)

    def test_disabled_http_never_resolves_or_connects(self):
        client = HTTP(Budget(POLICY["limits"]))
        with self.assertRaisesRegex(Problem,"NETWORK_DISABLED"): client.request(FIXTURE["feed"])

    def test_host_delay_applies_after_size_failure_too(self):
        client,_=self.client([WireResponse(200,b"too large"),WireResponse(200,b"ok")])
        before=self.clock.monotonic()
        with self.assertRaisesRegex(Problem,"RESPONSE_SIZE"):client.request("https://public.example/feed",limit=2)
        client.request("https://public.example/feed")
        self.assertGreaterEqual(self.clock.monotonic()-before,2)


class ContinuousTests(Offline):
    def test_full_pipeline_three_radars_68_existing_not_discovery_repeat_zero_adds(self):
        store, server = self.state(), Server()
        worker = self.worker(store, server)
        first = worker.cycle(apply=True, approved_policy_hash=POLICY_HASH)
        self.assertEqual(first["errors"], [])
        self.assertEqual(len(first["providers"]), 3)
        self.assertEqual(first["new_subscriptions"], 1)
        self.assertEqual(store.summary()["channel_profiles"], 1)
        self.assertEqual(store.summary()["observations"], 3)
        self.assertLessEqual(store.summary()["candidates"], 2)
        self.assertEqual(len(server.subs), 69)
        posts = list(server.posts)
        again = worker.cycle(apply=True, approved_policy_hash=POLICY_HASH)
        self.assertEqual(again["new_subscriptions"], 0)
        self.assertEqual(server.posts, posts)
        self.assertNotIn("OFFLINE_SECRET", json.dumps(first))
        self.assertEqual(store.profiles()[0]["lifecycle"], "CONNECTED")

    def test_observe_and_apply_policy_gates(self):
        store, server = self.state(), Server()
        worker = self.worker(store, server)
        worker.cycle()
        self.assertEqual(server.posts, [])
        with self.assertRaisesRegex(Problem,"APPLY_NOT_ADMITTED"):
            worker.cycle(apply=True, approved_policy_hash="wrong")
        worker.config["pilot_proofs"] = {"single":False,"batch3":False}
        report = worker.cycle(apply=True, approved_policy_hash=POLICY_HASH)
        self.assertIn("PILOT_PROOFS_REQUIRED",report["errors"])
        self.assertEqual(server.posts, [])

    def test_uncertain_write_does_not_repeat_and_reconciles_only_with_folder(self):
        store, server = self.state(), Server()
        worker = self.worker(store, server)
        server.mode = "timeout_after_add"
        first = worker.cycle(apply=True, approved_policy_hash=POLICY_HASH)
        self.assertEqual(first["writer_pending_reconciliation"],1)
        self.assertEqual(len(server.posts),1)
        store.close()
        store = self.state(bootstrap=False)
        worker = self.worker(store,server)
        next_run = worker.cycle(apply=True,approved_policy_hash=POLICY_HASH)
        self.assertIn("UNRESOLVED_WRITE_HOLD",next_run["errors"])
        self.assertEqual(len(server.posts),1)
        server.subs["feed/"+FIXTURE["feed"]]["categories"] = [{"label":"BOHUN_MEDIA"}]
        recovered = worker.cycle(apply=True,approved_policy_hash=POLICY_HASH)
        self.assertEqual(recovered["writer_pending_reconciliation"],0,recovered["errors"])
        self.assertEqual(len(server.posts),1)
        self.assertEqual(store.profiles()[0]["lifecycle"],"CONNECTED")

    def test_crash_after_folder_send_recovers_without_any_repeated_write(self):
        store, server = self.state(), Server()
        profile = self.profile(store)
        server.mode = "crash_after_folder"
        with self.assertRaises(Crash): self.bridge(store,server).connect(profile)
        self.assertEqual(len(store.unresolved()),1)
        posts = list(server.posts)
        store.close()
        store = self.state(bootstrap=False)
        self.assertTrue(self.bridge(store,server).reconcile())
        self.assertEqual(server.posts,posts)
        self.assertEqual(store.profiles()[0]["lifecycle"],"CONNECTED")

    def test_crash_before_send_still_holds_reserved_attempt_after_restart(self):
        store, server = self.state(), Server()
        profile = self.profile(store)
        server.mode = "crash_before_send"
        with self.assertRaises(Crash): self.bridge(store,server).connect(profile)
        self.assertEqual(server.posts,[])
        store.close()
        store = self.state(bootstrap=False)
        self.assertFalse(self.bridge(store,server).reconcile())
        self.assertEqual(server.posts,[])

    def test_five_daily_attempts_cap_survives_restart(self):
        store, server = self.state(),Server()
        for i in range(5):
            self.bridge(store,server).connect(self.profile(store,"https://publisher"+str(i)+".example/feed"))
        self.assertEqual(len([p for p in server.posts if p[0]=="quickadd"]),5)
        store.close()
        store = self.state(bootstrap=False)
        with self.assertRaises(Problem):
            self.bridge(store,server).connect(self.profile(store,"https://sixth.example/feed"))
        self.assertEqual(len([p for p in server.posts if p[0]=="quickadd"]),5)

    def test_two_fresh_collecting_cycles_required_for_active(self):
        store, server = self.state(),Server()
        worker = self.worker(store,server)
        worker.cycle(apply=True,approved_policy_hash=POLICY_HASH)
        self.clock.advance(5)
        record = {"id":"record1","saved_at":stamp(self.clock.now()),"origin_stream_id":"feed/"+FIXTURE["feed"],
                  "folder":"BOHUN_MEDIA","collector_cycle_id":"scheduled-cycle-1"}
        server.receipts[FIXTURE["feed"]]=[record]
        worker.cycle()
        self.assertEqual(store.profiles()[0]["delivery"]["state"],"FIRST_DELIVERY_PROVEN")
        worker.cycle()
        self.assertEqual(store.profiles()[0]["lifecycle"],"CONNECTED")
        self.clock.advance(3600)
        server.receipts[FIXTURE["feed"]].append(dict(record,id="record2",saved_at=stamp(self.clock.now()),
                                                    collector_cycle_id="scheduled-cycle-2"))
        worker.cycle()
        self.assertEqual(store.profiles()[0]["lifecycle"],"ACTIVE")

    def test_partial_stream_cursor_kept_when_later_page_fails(self):
        store, server = self.state(),Server()
        item={"id":"one","alternate":[{"href":FIXTURE["entries"][0]["url"]}]}
        server.stream_pages=[{"items":[item],"continuation":"opaque-next"},Problem("INOREADER_HTTP_429")]
        worker=self.worker(store,server)
        report=worker.cycle()
        self.assertEqual(store.cursor("ino")["continuation"],"opaque-next")
        self.assertEqual(report["providers"]["ino"]["new_observations"],1)
        self.assertIn("google",report["providers"])
        self.assertGreaterEqual(store.summary()["observations"],2)

    def test_serve_waits_and_stops_without_a_background_task(self):
        store,server=self.state(),Server()
        worker=self.worker(store,server)
        stop=threading.Event()
        waits,reports=[],[]
        def wait(seconds): waits.append(seconds);stop.set()
        serve(worker,stop=stop,wait=wait,on_report=reports.append)
        self.assertEqual(waits,[3600])
        self.assertEqual(len(reports),1)
        self.assertEqual(server.posts,[])

    def test_missing_destination_prevents_subscription(self):
        store,server=self.state(),Server()
        worker=self.worker(store,server)
        worker.delivery_reader=lambda p: (_ for _ in ()).throw(Problem("DB_DRIVER_UNAVAILABLE"))
        report=worker.cycle(apply=True,approved_policy_hash=POLICY_HASH)
        self.assertIn("DB_DRIVER_UNAVAILABLE",report["errors"])
        self.assertEqual(server.posts,[])

    def test_oauth_disabled_and_manager_hash_checked_without_secrets(self):
        with patch.object(os.environ,"get",side_effect=blocked):
            with self.assertRaisesRegex(Problem,"OAUTH_DISABLED"): oauth_token(None,{})
        with self.assertRaisesRegex(Problem,"MANAGER_HASH_MISMATCH"): load_manager(FIELD,"wrong")


class DeliveryTests(Offline):
    def connected(self):
        profile=self.feed()
        profile["connection"]={"membership_verified":True,"folder_verified":True,
                               "folder":"BOHUN_MEDIA","connected_at":self.clock.now()-60}
        profile["lifecycle"]="CONNECTED"
        return profile

    def test_wrong_origin_folder_old_records_do_not_prove_delivery(self):
        profile=self.connected()
        receipt={"read_only":True,"origin_stream_id":"feed/"+profile["feed_url"],"folder":"BOHUN_MEDIA",
                 "records":[{"id":"old","saved_at":stamp(self.clock.now()-1000),
                              "origin_stream_id":"feed/"+profile["feed_url"],"folder":"BOHUN_MEDIA"},
                             {"id":"wrong","saved_at":stamp(self.clock.now()),
                              "origin_stream_id":"feed/https://other.example/feed","folder":"BOHUN_MEDIA"}]}
        self.assertEqual(evaluate(profile,receipt,self.clock.now())["state"],"WAITING_DELIVERY")
        receipt["folder"]="BOHUN_GOV"
        with self.assertRaisesRegex(Problem,"DELIVERY_SCOPE"):evaluate(profile,receipt,self.clock.now())

    def test_new_records_without_collector_cycle_id_never_active(self):
        profile=self.connected()
        receipt={"read_only":True,"origin_stream_id":"feed/"+profile["feed_url"],"folder":"BOHUN_MEDIA",
                 "records":[{"id":str(i),"saved_at":stamp(self.clock.now()-i),
                              "origin_stream_id":"feed/"+profile["feed_url"],"folder":"BOHUN_MEDIA"} for i in range(5)]}
        profile["delivery"]=evaluate(profile,receipt,self.clock.now())
        self.assertEqual(profile["delivery"]["state"],"FIRST_DELIVERY_PROVEN")
        self.assertEqual(evaluate(profile,receipt,self.clock.now()+3600)["lifecycle"],"CONNECTED")

    def test_readonly_query_checks_transport_and_exact_origin_with_bound_limit(self):
        profile=self.connected()
        statements=[]
        class Cursor:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def execute(self,query,params=None):statements.append((query,params))
            def fetchone(self):return ("Inoreader BOHUN","https://www.inoreader.com/")
            def fetchmany(self,n):
                self_n=n
                return [("one",stamp(self_outer.clock.now()),"feed/"+profile["feed_url"],"BOHUN_MEDIA")]
        class Connection:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def transaction(self):return contextlib.nullcontext()
            def cursor(self):return Cursor()
        self_outer=self
        result=read_delivery(Connection,{"transport_source_id":2},profile)
        self.assertEqual(statements[0][0],"SET TRANSACTION READ ONLY")
        self.assertIn("LIMIT 20",statements[-1][0])
        self.assertIn("feed/"+profile["feed_url"],statements[-1][1])
        self.assertTrue(result["read_only"])
        self.assertIsNone(result["records"][0]["collector_cycle_id"])

    def test_optional_database_disabled_without_loading_driver(self):
        with self.assertRaisesRegex(Problem,"DB_DISABLED"): optional_connect({})


class AdditionalGuards(Offline):
    def runtime_config(self, root, state=None):
        return {"version":1, "policy_path":str(PACKAGE/"policy.proposed.json"),
                "runtime_root":str(root), "state_path":str(state or root/"sandbox/service"),
                "deployment_id":"g5-unit-test", "live_inputs":False, "writer_enabled":False,
                "providers":[], "artifacts":[]}

    def test_cli_requires_explicit_runtime_root(self):
        config=self.runtime_config(self.root)
        config.pop("runtime_root")
        path=self.root/"missing-root.json"
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(Problem,"RUNTIME_ROOT_REQUIRED"):
            main(["once","--config",str(path),"--bootstrap"])
        self.assertFalse((self.root/"sandbox").exists())

    def test_cli_bootstraps_only_external_private_state(self):
        config=self.runtime_config(self.root)
        path=self.root/"external-root.json"
        path.write_text(json.dumps(config))
        output=io.StringIO()
        get_env=os.environ.get
        def guarded_env(key,*args):
            if any(term in key.upper() for term in ("INOREADER","SUPABASE","TOKEN","PASSWORD","SECRET","API_KEY")):
                blocked()
            return get_env(key,*args)
        with contextlib.redirect_stdout(output),patch.object(os.environ,"get",side_effect=guarded_env):
            code=main(["once","--config",str(path),"--bootstrap"])
        report=json.loads(output.getvalue())
        self.assertEqual(code,0)
        self.assertEqual(report["execution"],"OFFLINE")
        self.assertEqual(report["http"]["requests"],0)
        self.assertEqual(report["producer"]["integration"],"REPOSITORY_PACKAGE")
        self.assertTrue((self.root/"sandbox/service/state.sqlite").is_file())

    def test_cli_rejects_repository_and_public_state_roots(self):
        roots=[FIELD,self.root/"public",self.root/"dist",self.root/".git"]
        for root in roots:
            with self.subTest(root=root):
                config=self.runtime_config(root)
                path=self.root/"unsafe-root.json"
                path.write_text(json.dumps(config))
                with self.assertRaisesRegex(Problem,"PRIVATE_STATE_ROOT"):
                    main(["once","--config",str(path),"--bootstrap"])
        self.assertFalse((FIELD/"sandbox/service/state.sqlite").exists())

    def test_cli_rejects_symlink_runtime_root(self):
        real=self.root/"real"
        real.mkdir()
        link=self.root/"linked"
        link.symlink_to(real,target_is_directory=True)
        path=self.root/"symlink-root.json"
        path.write_text(json.dumps(self.runtime_config(link)))
        with self.assertRaisesRegex(Problem,"PATH_SYMLINK"):
            main(["once","--config",str(path),"--bootstrap"])
        self.assertFalse((real/"sandbox").exists())

    def test_exact_alias_and_other_existing_host_profile_are_held_without_add(self):
        for alias in (True,False):
            with self.subTest(alias=alias):
                store=self.state(name="alias"+str(alias))
                server=Server()
                profile=self.profile(store)
                old="https://publisher.example/old-feed"
                server.subs["feed/"+old]={"id":"feed/"+old,"categories":[{"label":"BOHUN_MEDIA"}]}
                if alias:profile["aliases"]=[old]
                with self.assertRaisesRegex(Problem,"EXISTING_ALIAS_REVIEW" if alias else "EXISTING_HOST_PROFILE_REVIEW"):
                    self.bridge(store,server).connect(profile)
                self.assertEqual(server.posts,[])

    def test_identical_observed_windows_hold_second_endpoint_without_permanent_merge(self):
        store=self.state()
        self.profile(store)
        second=self.profile(store,"https://publisher.example/atom")
        self.assertEqual(len(store.profiles()),2)
        self.assertEqual(second["assessment"]["decision"],"REVIEW")
        self.assertIn("gate:duplicate-observed-window",second["assessment"]["rule_ids"])

    def test_aggregator_origin_feed_is_not_registered_as_publisher(self):
        obs=publisher_observation({"id":"a","url":FIXTURE["entries"][0]["url"],
                                   "origin":{"streamId":"feed/https://news.google.com/rss/search?q=defence"}},"inoreader")
        self.assertEqual(obs["publisher_url"],FIXTURE["publisher"])
        self.assertNotIn("feed_url",obs)

    def test_repeated_title_spam_does_not_meet_sample_minimum(self):
        feed=self.feed()
        feed["entries"]=[dict(e,title="Defence procurement contract award") for e in feed["entries"]]
        result=assess(feed,POLICY,self.clock.now())
        self.assertEqual(result["sample_size"],1)
        self.assertEqual(result["decision"],"REVIEW")

    def test_expired_continuation_requests_resync_without_claiming_coverage(self):
        server=Server()
        server.radar_errors["inoreader"]=400
        budget=Budget(POLICY["limits"],monotonic=self.clock.monotonic)
        http=FakeHTTP(server,budget,lambda _:None)
        config={"type":"inoreader","stream":FIXTURE["radars"]["inoreader_stream"]}
        pages=list(radar_pages(config,{"continuation":"expired","seen_ids":["kept"]},http,lambda:"OFFLINE_SECRET",POLICY["limits"]))
        self.assertEqual(pages[0][0],[])
        self.assertIsNone(pages[0][1]["continuation"])
        self.assertEqual(pages[0][1]["coverage"],"RESYNC_REQUIRED")
        self.assertEqual(pages[0][1]["seen_ids"],["kept"])
        self.assertEqual(len(server.requests),1)

    def test_directory_provenance_preserved_and_last_seen_updates(self):
        path=self.root/"directory.csv"
        path.write_text("website,directory_page\nhttps://publisher.example/,https://directory.example/members\n")
        rows,cursor=artifact_page({"path":str(path)}, {},100)
        store=self.state()
        store.ingest("directory","q",rows,cursor,self.clock.now())
        self.clock.advance(10)
        store.ingest("directory","q",rows,cursor,self.clock.now())
        row=store.db.execute("SELECT payload,seen,last_seen FROM observations").fetchone()
        self.assertEqual(json.loads(row[0])["source_page"],"https://directory.example/members")
        self.assertEqual(row[2]-row[1],10)

    def test_api_429_blocks_writer_but_other_radars_continue(self):
        store,server=self.state(),Server()
        server.radar_errors["inoreader"]=429
        report=self.worker(store,server).cycle(apply=True,approved_policy_hash=POLICY_HASH)
        self.assertIn("INOREADER_WRITER_BLOCKED",report["errors"])
        self.assertIn("google",report["providers"])
        self.assertIn("talk",report["providers"])
        self.assertEqual(server.posts,[])

    def test_budget_does_not_reset_when_clock_moves_backwards(self):
        store=self.state()
        store.reserve("one","run","quickadd",self.clock.now(),day_limit=5,cycle_limit=1)
        with self.assertRaisesRegex(Problem,"CLOCK_REVERSED"):
            store.reserve("two","run2","quickadd",self.clock.now()-86400,day_limit=5,cycle_limit=1)

    def test_corrupt_journal_stops_and_releases_lock(self):
        store=self.state()
        store.close()
        (self.root/"worker/state.sqlite").write_bytes(b"not a sqlite database")
        for _ in range(2):
            with self.assertRaisesRegex(Problem,"STATE_CORRUPT_OR_SCHEMA"):
                Store(self.root/"worker","test-worker")

    def test_missing_receipt_does_not_create_fresh_health_success(self):
        feed=self.feed()
        feed["connection"]={"membership_verified":True,"folder_verified":True,"folder":"BOHUN_MEDIA","connected_at":0}
        feed["lifecycle"]="ACTIVE"
        feed["delivery"]={"state":"OK","checked_at":10,"cycles":[]}
        result=evaluate(feed,None,self.clock.now())
        self.assertEqual(result["state"],"UNKNOWN")
        self.assertEqual(result["checked_at"],10)

    def test_empty_live_config_uses_no_credentials_or_network(self):
        store,server=self.state(),Server()
        worker=self.worker(store,server,live_inputs=False,writer_enabled=False,providers=[])
        with patch.object(os.environ,"get",side_effect=blocked):
            report=worker.cycle()
        self.assertEqual(report["http"]["requests"],0)
        self.assertEqual(report["providers"],{})
        self.assertEqual(server.posts,[])

    def test_db_preflight_scope_mismatch_blocks_writer(self):
        store,server=self.state(),Server()
        worker=self.worker(store,server)
        worker.delivery_reader=lambda p:{"read_only":True,"origin_stream_id":"wrong","folder":"BOHUN_MEDIA","records":[]}
        report=worker.cycle(apply=True,approved_policy_hash=POLICY_HASH)
        self.assertIn("DELIVERY_READER_SCOPE",report["errors"])
        self.assertEqual(server.posts,[])

    def test_foreground_serve_halts_on_stuck_dns_without_restarting_threads(self):
        reports,waits=[],[]
        worker=SimpleNamespace(policy=POLICY,cycle=lambda **kw:{"errors":["DNS_TIMEOUT"]})
        serve(worker,wait=waits.append,on_report=reports.append)
        self.assertEqual(len(reports),1)
        self.assertEqual(waits,[])

    def test_cli_rejects_state_outside_private_sandbox(self):
        config={"version":1,"policy_path":str(PACKAGE/"policy.proposed.json"),"runtime_root":str(self.root),
                "state_path":str(self.root/"bad"),
                "deployment_id":"bad","providers":[],"artifacts":[]}
        path=self.root/"config.json"
        path.write_text(json.dumps(config))
        with self.assertRaisesRegex(Problem,"PRIVATE_STATE_ROOT"):
            main(["once","--config",str(path),"--bootstrap"])

    def test_tls_pinning_uses_original_hostname_and_verified_ip_without_resolving_again(self):
        from collectors.source_registry.discovery.radars import PinnedConnection
        calls=[]
        class Raw:
            def settimeout(self,n):calls.append(("timeout",n))
            def connect(self,target):calls.append(("connect",target))
            def close(self):pass
        class TLS:
            def do_handshake(self):calls.append(("handshake",True))
        class Context:
            def wrap_socket(self,raw,**kwargs):
                calls.append(("sni",kwargs["server_hostname"]))
                return TLS()
        connection=PinnedConnection("publisher.example","8.8.8.8",socket.AF_INET,12)
        connection._context=Context()
        with patch.object(socket,"socket",return_value=Raw()):connection.connect()
        self.assertIn(("connect",("8.8.8.8",443)),calls)
        self.assertIn(("sni","publisher.example"),calls)
        self.assertIn(("handshake",True),calls)

    def test_source_manager_loader_is_readonly_and_hash_pinned(self):
        path=FIELD/"collectors/inoreader/inoreader_source_manager.py"
        module=load_manager(FIELD,hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(callable(module.run))
        self.assertEqual(module.FOLDERS,manager.FOLDERS)


if __name__=="__main__":
    unittest.main()
