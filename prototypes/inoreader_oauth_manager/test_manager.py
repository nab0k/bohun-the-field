"""Offline tests. No credentials, Supabase or Inoreader requests."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import importlib.util
from threading import Lock
import unittest

spec = importlib.util.spec_from_file_location("oauth_manager", Path(__file__).with_name("manager.py"))
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class FakeOAuth:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.seen = []

    def refresh(self, token):
        self.seen.append(token)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


class FailingStore(module.MemoryStore):
    def replace(self, expected_generation, new_refresh_token):
        raise OSError("simulated failure")


class TokenManagerTests(unittest.TestCase):
    def manager(self, store, client):
        return module.TokenManager(store, client, Lock())

    def test_unchanged_token(self):
        store = module.MemoryStore("old")
        self.assertEqual(self.manager(store, FakeOAuth([module.OAuthResult("access")])).refresh(), "access")
        self.assertEqual(store.read().generation, 0)

    def test_rotation_is_persisted(self):
        store = module.MemoryStore("old")
        self.assertEqual(self.manager(store, FakeOAuth([module.OAuthResult("access", "new")])).refresh(), "access")
        self.assertEqual(store.read(), module.TokenState("new", 1))

    def test_persistence_failure_requires_recovery(self):
        store = FailingStore("old")
        manager = self.manager(store, FakeOAuth([module.OAuthResult("access", "new")]))
        with self.assertRaises(module.TokenPersistenceFailed):
            manager.refresh()
        with self.assertRaises(module.RecoveryRequired):
            manager.refresh()

    def test_request_timeout_is_uncertain(self):
        store = module.MemoryStore("old")
        manager = self.manager(store, FakeOAuth([TimeoutError("network")]))
        with self.assertRaises(module.RefreshFailed):
            manager.refresh()
        self.assertTrue(store.is_uncertain())

    def test_missing_access_token(self):
        store = module.MemoryStore("old")
        with self.assertRaises(module.RecoveryRequired):
            self.manager(store, FakeOAuth([module.OAuthResult("", "new")])).refresh()
        self.assertTrue(store.is_uncertain())

    def test_generation_mismatch(self):
        store = module.MemoryStore("old")
        with self.assertRaises(ValueError):
            store.replace(9, "new")

    def test_concurrent_refresh_serialized(self):
        store = module.MemoryStore("old")
        client = FakeOAuth([module.OAuthResult("a1", "new"), module.OAuthResult("a2", "newer")])
        manager = self.manager(store, client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: manager.refresh(), range(2))), ["a1", "a2"])
        self.assertEqual(client.seen, ["old", "new"])
        self.assertEqual(store.read().generation, 2)


if __name__ == "__main__":
    unittest.main()
