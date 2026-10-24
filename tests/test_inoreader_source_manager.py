import contextlib
import copy
import importlib.util
import io
import json
import socket
import tempfile
import unittest
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

def blocked_transport(*args, **kwargs):
    raise AssertionError("Real transport forbidden in Source Manager offline tests")

with patch.object(urllib.request, "urlopen", side_effect=blocked_transport), \
     patch.object(socket, "getaddrinfo", side_effect=blocked_transport):
    from collectors.inoreader import inoreader_source_manager as m

class OfflineCase(unittest.TestCase):
    def setUp(self):
        for owner, name in ((urllib.request, "urlopen"), (socket, "getaddrinfo"),
                            (socket, "create_connection"), (socket.socket, "connect")):
            guard = patch.object(owner, name, side_effect=blocked_transport)
            guard.start()
            self.addCleanup(guard.stop)

class SourceManagerTests(OfflineCase):
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


class FakeProvider:
    """Stateful server: a write can take effect even when its response is lost."""
    def __init__(self):
        self.subs = {}
        self.posts = []
        self.discovered = []
        self.auth_calls = self.reads = 0
        self.quickadd_mode = "ok"
        self.quickadd_modes = {}
        self.folder_mode = "ok"
        self.fail_reads = set()
        self.snapshot_hook = None

    def auth(self):
        self.auth_calls += 1
        return "OFFLINE_TOKEN_DO_NOT_LOG"

    def discover(self, source):
        self.discovered.append(source["name"])
        return dict(source, status="validated_feed", feed_url=source["url"])

    def add_existing(self, url, folder=None):
        self.subs["feed/" + url] = {"id": "feed/" + url,
                                   "categories": [{"label": folder}] if folder else []}

    def api(self, path, token):
        assert path == "subscription/list"
        self.reads += 1
        if self.reads in self.fail_reads:
            raise RuntimeError("OFFLINE_TOKEN_DO_NOT_LOG")
        if self.snapshot_hook:
            self.snapshot_hook(self)
        return {"subscriptions": copy.deepcopy(list(self.subs.values()))}

    def post(self, path, token, values):
        self.posts.append((path, dict(values)))
        if path == "subscription/quickadd":
            feed = "feed/" + values["quickadd"]
            self.add_existing(values["quickadd"])
            mode = self.quickadd_modes.get(values["quickadd"], self.quickadd_mode)
            if mode == "exception":
                raise RuntimeError("OFFLINE_TOKEN_DO_NOT_LOG")
            if mode == "empty":
                return "{}"
            if mode == "list":
                return "[]"
            if mode == "non_json":
                return "not JSON"
            if mode == "alias":
                return json.dumps({"streamId": "feed/https://alias.test/feed"})
            return json.dumps({"streamId": feed})
        assert path == "subscription/edit"
        if self.folder_mode == "exception":
            raise RuntimeError("OFFLINE_TOKEN_DO_NOT_LOG")
        if self.folder_mode != "silent_noop":
            self.subs[values["s"]]["categories"] = [{"id": values["a"]}]
        return "OK"

    def quickadds(self):
        return [values for path, values in self.posts if path == "subscription/quickadd"]


class ApplyTests(OfflineCase):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.provider = FakeProvider()

    def sources(self, count=1):
        return [{"name": "Source " + str(i), "url": "https://example.test/feed/" + str(i),
                 "group": "Media / intelligence"} for i in range(count)]

    def invoke(self, sources=None, only=None, apply=True, check=True, limit=1):
        sources = self.sources() if sources is None else sources
        registry = self.directory / "registry.json"
        report = self.directory / "report.json"
        registry.write_text(json.dumps({"sources": sources}), encoding="utf-8")
        args = SimpleNamespace(registry=str(registry), report=str(report), workers=1,
                               apply=apply, check_subscriptions=check, max_add=limit,
                               only=",".join(s["name"] for s in sources) if only is None else only)
        output = io.StringIO()
        with patch.object(m, "discover", side_effect=self.provider.discover), \
             patch.object(m, "access_token", side_effect=self.provider.auth), \
             patch.object(m, "api", side_effect=self.provider.api), \
             patch.object(m, "post_api", side_effect=self.provider.post), \
             contextlib.redirect_stdout(output):
            code = m.run(args)
        return code, json.loads(report.read_text()), output.getvalue()

    def test_single_add_is_verified_from_server_categories(self):
        code, report, output = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["added"], 1)
        self.assertEqual(report["summary"]["add_attempts"], 1)
        self.assertTrue(report["results"][0]["membership_verified"])
        self.assertTrue(report["results"][0]["folder_verified"])
        self.assertEqual(self.provider.reads, 2)
        self.assertNotIn("OFFLINE_TOKEN", json.dumps(report) + output)

    def test_repeat_does_not_write_or_duplicate(self):
        self.invoke()
        previous_posts = list(self.provider.posts)
        code, report, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["already_subscribed"], 1)
        self.assertEqual(report["summary"]["add_attempts"], 0)
        self.assertEqual(self.provider.posts, previous_posts)
        self.assertEqual(len(self.provider.subs), 1)

    def test_normal_cap_one_leaves_rest_pending(self):
        code, report, _ = self.invoke(self.sources(3))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.provider.quickadds()), 1)
        self.assertEqual([x["status"] for x in report["results"]],
                         ["added", "pending_limit", "pending_limit"])

    def test_three_source_batch_and_repeat_are_idempotent(self):
        sources = self.sources(3)
        code, report, _ = self.invoke(sources, limit=3)
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["added"], 3)
        self.assertTrue(all(x["folder_verified"] for x in report["results"]))
        previous_posts = list(self.provider.posts)
        code, report, _ = self.invoke(sources, limit=3)
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["already_subscribed"], 3)
        self.assertEqual(report["summary"]["add_attempts"], 0)
        self.assertEqual(self.provider.posts, previous_posts)

    def test_partial_batch_keeps_success_and_repeat_does_not_readd_incomplete(self):
        sources = self.sources(3)
        self.provider.quickadd_modes[sources[1]["url"]] = "empty"
        code, report, _ = self.invoke(sources, limit=3)
        self.assertEqual(code, 2)
        self.assertEqual([x["status"] for x in report["results"]],
                         ["added", "add_unconfirmed", "pending_after_failure"])
        self.assertEqual(report["summary"]["add_attempts"], 2)
        self.assertEqual(len(self.provider.subs), 2)
        previous_posts = list(self.provider.posts)
        code, report, _ = self.invoke(sources, limit=3)
        self.assertEqual(code, 2)
        self.assertEqual([x["status"] for x in report["results"]],
                         ["already_subscribed", "connection_incomplete", "pending_after_failure"])
        self.assertEqual(self.provider.posts, previous_posts)

    def test_unknown_add_response_consumes_attempt_and_stops_batch(self):
        self.provider.quickadd_mode = "empty"
        code, report, _ = self.invoke(self.sources(3))
        self.assertEqual(code, 2)
        self.assertEqual(len(self.provider.quickadds()), 1)
        self.assertEqual(len(self.provider.subs), 1)
        self.assertEqual(report["summary"]["add_attempts"], 1)
        self.assertEqual(report["summary"]["stop_reason"], "add_outcome_unknown")
        self.assertTrue(report["results"][0]["membership_verified"])
        self.assertFalse(report["results"][0]["folder_verified"])
        self.assertEqual([x["status"] for x in report["results"]][1:],
                         ["pending_after_failure", "pending_after_failure"])

    def test_unknown_add_stops_even_with_remaining_budget(self):
        self.provider.quickadd_mode = "empty"
        code, report, _ = self.invoke(self.sources(3), limit=3)
        self.assertEqual(code, 2)
        self.assertEqual(report["summary"]["add_attempts"], 1)

    def test_bad_json_and_non_object_responses_do_not_escape_or_retry(self):
        for mode in ("non_json", "list"):
            with self.subTest(mode=mode):
                self.provider = FakeProvider()
                self.provider.quickadd_mode = mode
                code, report, _ = self.invoke(self.sources(3))
                self.assertEqual(code, 2)
                self.assertEqual(len(self.provider.posts), 1)
                self.assertEqual(report["results"][0]["status"], "add_unconfirmed")

    def test_lost_add_response_does_not_leak_error_or_retry(self):
        self.provider.quickadd_mode = "exception"
        code, report, output = self.invoke(self.sources(3), limit=3)
        self.assertEqual(code, 2)
        self.assertEqual(len(self.provider.posts), 1)
        self.assertNotIn("OFFLINE_TOKEN", json.dumps(report) + output)

    def test_unexpected_stream_id_never_receives_folder_edit(self):
        self.provider.quickadd_mode = "alias"
        code, report, _ = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(len(self.provider.posts), 1)
        self.assertEqual(report["summary"]["folder_edit_attempts"], 0)

    def test_folder_exception_stops_and_repeat_cannot_claim_success(self):
        self.provider.folder_mode = "exception"
        sources = self.sources(3)
        code, report, output = self.invoke(sources, limit=3)
        self.assertEqual(code, 2)
        self.assertEqual(len(self.provider.quickadds()), 1)
        self.assertEqual(report["results"][0]["folder_status"], "assignment_failed")
        self.assertNotIn("OFFLINE_TOKEN", json.dumps(report) + output)
        previous_posts = list(self.provider.posts)
        code, report, _ = self.invoke(sources, limit=3)
        self.assertEqual(code, 2)
        self.assertEqual(report["results"][0]["status"], "connection_incomplete")
        self.assertEqual(report["summary"]["stop_reason"], "existing_folder_missing")
        self.assertEqual(self.provider.posts, previous_posts)

    def test_folder_http_ack_is_not_membership_evidence(self):
        self.provider.folder_mode = "silent_noop"
        code, report, _ = self.invoke(self.sources(3), limit=3)
        self.assertEqual(code, 2)
        self.assertEqual(report["summary"]["added"], 0)
        self.assertFalse(report["results"][0]["folder_verified"])
        self.assertEqual(report["results"][0]["folder_status"], "not_confirmed")
        self.assertEqual(len(self.provider.quickadds()), 1)

    def test_readback_failure_is_unknown_and_stops_remaining_writes(self):
        self.provider.fail_reads = {2}
        code, report, output = self.invoke(self.sources(3), limit=3)
        self.assertEqual(code, 2)
        self.assertEqual(len(self.provider.quickadds()), 1)
        self.assertIsNone(report["results"][0]["membership_verified"])
        self.assertIsNone(report["summary"]["subscriptions_after"])
        self.assertNotIn("OFFLINE_TOKEN", json.dumps(report) + output)

    def test_readback_missing_subscription_stops_new_writes(self):
        def remove_after_write(provider):
            if provider.reads == 2:
                provider.subs.clear()
        self.provider.snapshot_hook = remove_after_write
        code, report, _ = self.invoke(self.sources(3), limit=3)
        self.assertEqual(code, 2)
        self.assertEqual(len(self.provider.quickadds()), 1)
        self.assertFalse(report["results"][0]["membership_verified"])
        self.assertEqual(report["summary"]["stop_reason"], "connection_not_confirmed")

    def test_final_snapshot_also_checks_earlier_successes(self):
        def remove_first_folder(provider):
            if provider.reads == 3:
                provider.subs["feed/" + self.sources()[0]["url"]]["categories"] = []
        self.provider.snapshot_hook = remove_first_folder
        code, report, _ = self.invoke(self.sources(2), limit=2)
        self.assertEqual(code, 2)
        self.assertEqual(report["results"][0]["status"], "connection_incomplete")
        self.assertEqual(report["results"][1]["status"], "added")
        self.assertEqual(report["summary"]["stop_reason"], "final_state_mismatch")

    def test_selection_precedes_discovery(self):
        code, report, _ = self.invoke(self.sources(3), only="source 1")
        self.assertEqual(code, 0)
        self.assertEqual(self.provider.discovered, ["Source 1"])
        self.assertEqual([x["status"] for x in report["results"]],
                         ["not_selected", "added", "not_selected"])

    def test_empty_and_unknown_selection_fail_before_transport(self):
        for only in ("", "Missing", "Source 0,Missing"):
            with self.subTest(only=only), self.assertRaises(ValueError):
                self.invoke(only=only)
        self.assertEqual(self.provider.auth_calls, 0)
        self.assertEqual(self.provider.discovered, [])
        self.assertEqual(self.provider.posts, [])

    def test_empty_registry_cannot_apply(self):
        with self.assertRaises(ValueError):
            self.invoke([], only="Missing")
        self.assertEqual(self.provider.auth_calls, 0)

    def test_invalid_group_and_url_fail_before_discovery(self):
        for key, value in (("group", "Unknown"), ("url", "http://example.test/feed")):
            sources = self.sources()
            sources[0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.invoke(sources)
        self.assertEqual(self.provider.discovered, [])
        self.assertEqual(self.provider.posts, [])

    def test_duplicate_input_does_not_spend_another_attempt(self):
        sources = self.sources(2)
        sources[1]["url"] = sources[0]["url"]
        code, report, _ = self.invoke(sources, limit=2)
        self.assertEqual(code, 0)
        self.assertEqual(report["results"][1]["status"], "duplicate_input")
        self.assertEqual(len(self.provider.quickadds()), 1)

    def test_same_feed_conflicting_folders_blocks_before_oauth(self):
        sources = self.sources(2)
        sources[1].update(url=sources[0]["url"], group="Industry / corporates")
        code, report, _ = self.invoke(sources, limit=2)
        self.assertEqual(code, 2)
        self.assertEqual(self.provider.auth_calls, 0)
        self.assertEqual(self.provider.posts, [])
        self.assertEqual([x["status"] for x in report["results"]],
                         ["identity_conflict", "identity_conflict"])

    def test_distinct_feeds_on_same_host_remain_distinct(self):
        code, report, _ = self.invoke(self.sources(2), limit=2)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.provider.subs), 2)
        self.assertEqual(report["summary"]["added"], 2)

    def test_initial_read_failure_cannot_write(self):
        self.provider.fail_reads = {1}
        code, report, output = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(self.provider.posts, [])
        self.assertEqual(report["summary"]["stop_reason"], "initial_snapshot_failed")
        self.assertNotIn("OFFLINE_TOKEN", json.dumps(report) + output)

    def test_oauth_failure_is_sanitized_and_cannot_write(self):
        def fail_auth():
            raise RuntimeError("OFFLINE_TOKEN_DO_NOT_LOG")
        self.provider.auth = fail_auth
        code, report, output = self.invoke()
        self.assertEqual(code, 2)
        self.assertEqual(report["summary"]["stop_reason"], "oauth_failed")
        self.assertEqual(report["auth_error_type"], "RuntimeError")
        self.assertEqual(self.provider.posts, [])
        self.assertNotIn("OFFLINE_TOKEN", json.dumps(report) + output)

    def test_malformed_snapshot_is_not_treated_as_an_empty_account(self):
        for payload in ({}, {"subscriptions": [{}]},
                        {"subscriptions": [{"id": "feed/x", "categories": "bad"}]},
                        {"subscriptions": [{"id": "feed/x"}, {"id": "feed/x"}]}):
            with self.subTest(payload=payload):
                self.provider.api = lambda *a, payload=payload: payload
                code, report, _ = self.invoke()
                self.assertEqual(code, 2)
                self.assertEqual(report["summary"]["stop_reason"], "initial_snapshot_failed")
        self.assertEqual(self.provider.posts, [])

    def test_dry_run_without_subscription_check_has_no_api_calls(self):
        code, report, _ = self.invoke(apply=False, check=False)
        self.assertEqual(code, 0)
        self.assertEqual(self.provider.auth_calls, 0)
        self.assertEqual(self.provider.reads, 0)
        self.assertEqual(self.provider.posts, [])
        self.assertIsNone(report["summary"]["subscriptions_before"])

    def test_read_only_check_verifies_existing_folder_without_posts(self):
        self.provider.add_existing(self.sources()[0]["url"], "BOHUN_MEDIA")
        code, report, _ = self.invoke(apply=False)
        self.assertEqual(code, 0)
        self.assertTrue(report["results"][0]["folder_verified"])
        self.assertEqual(self.provider.posts, [])

    def test_dry_run_does_not_turn_unknown_group_into_live_admission(self):
        sources = self.sources()
        sources[0]["group"] = "Unknown"
        code, report, _ = self.invoke(sources, apply=False, check=False)
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["added"], 0)
        self.assertEqual(self.provider.posts, [])

    def test_folder_identity_accepts_label_or_reader_category_id(self):
        self.assertTrue(m.has_folder({"categories": [{"label": "BOHUN_MEDIA"}]}, "BOHUN_MEDIA"))
        self.assertTrue(m.has_folder({"categories": [{"id": "user/123/label/BOHUN_MEDIA"}]}, "BOHUN_MEDIA"))
        self.assertFalse(m.has_folder({"categories": [{"label": "BOHUN_MEDIAX"}]}, "BOHUN_MEDIA"))

    def test_existing_ids_retains_public_return_contract(self):
        self.provider.add_existing(self.sources()[0]["url"])
        with patch.object(m, "api", side_effect=self.provider.api):
            ids, count = m.existing_ids("OFFLINE_TOKEN_DO_NOT_LOG")
        self.assertEqual(ids, {"feed/" + self.sources()[0]["url"]})
        self.assertEqual(count, 1)

    def test_legacy_importers_can_import_unchanged_shared_helpers(self):
        import sys
        sync_stub = SimpleNamespace(access_token=blocked_transport, api=blocked_transport)
        with patch.dict(sys.modules, {"inoreader_source_manager": m, "sync": sync_stub}):
            for name in ("import_all17", "import_extra8"):
                spec = importlib.util.spec_from_file_location("offline_" + name,
                        Path(m.__file__).with_name(name + ".py"))
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self.assertIs(module.fetch, m.fetch)
                self.assertIs(module.post_api, m.post_api)
                self.assertIs(module.FOLDERS, m.FOLDERS)

    def test_invalid_attempt_limit_cannot_reach_transport(self):
        for limit in (0, 101):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.invoke(limit=limit)
        self.assertEqual(self.provider.auth_calls, 0)
        self.assertEqual(self.provider.posts, [])

if __name__ == "__main__":
    unittest.main()
