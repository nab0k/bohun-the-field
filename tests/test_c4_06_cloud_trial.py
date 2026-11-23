"""Offline regression cases for the isolated two-source cloud trial."""

from hashlib import sha256
import json
import unittest
from unittest.mock import patch

from collectors.signals import c4_06_cloud_trial as trial


def capture(text):
    return {"content_capture": {
        "version": 1, "provider_field": "summary.content",
        "status": "BODY_PRESENT_COMPLETENESS_UNVERIFIED",
        "text": text, "text_sha256": sha256(text.encode()).hexdigest(),
    }}


class CloudTrialTests(unittest.TestCase):
    def setUp(self):
        audit = capture(("A published audit update describes a change. " * 8) +
                        "For the first time in fiscal 2026 there was a clean opinion for the "
                        "Medicare-Eligible Retiree Health Care Fund.")
        bill = capture(("A parliamentary reporting fixture. " * 10) +
                       "A senator proposed a substitute amendment. The bill passed the House on Veterans Day.")
        self.rows = [(1, trial.AUDIT, "2026-11-17", audit), (2, trial.AUDIT, "2026-11-17", audit),
                     (3, trial.BILL, "2026-11-20", bill)]
        self.pins = {url: data["content_capture"]["text_sha256"] for _, url, _, data in self.rows}
        self.calls = []

    def checker(self, payload):
        self.calls.append(payload)
        message = json.loads(payload["messages"][1]["content"])
        self.assertEqual(set(message), {"request_sha256", "evidence", "fact_keys"})
        self.assertEqual(payload["max_completion_tokens"], 640)
        return {"request_sha256": message["request_sha256"], "verdict": "SUPPORTED",
                "facts": trial.FACTS, "reason": "Offline fixture"}

    def replay(self, check=None):
        with patch.dict(trial.PINNED_TEXT, self.pins):
            return trial.run(self.rows, self.checker if check is None else check)

    def test_equal_rows_produce_only_one_request_one_card(self):
        public, private = self.replay()
        self.assertEqual(public["rows_read"], 3)
        self.assertEqual((public["provider_attempts"], len(self.calls), public["queue_count"]), (1, 1, 1))
        self.assertEqual(public["decisions"][0]["duplicate_rows"], 1)
        self.assertEqual(public["decisions"][0]["repeat_added_rows"], 0)
        self.assertEqual(public["decisions"][1]["status"], "MONITORING")
        self.assertEqual(private["private_queue"][0]["visibility"], "private_pilot")
        self.assertNotIn("evidence", json.dumps(public))

    def test_conflicting_duplicate_body_holds_before_the_model(self):
        self.rows[1] = (2, trial.AUDIT, "2026-11-17", capture("Conflict in stored article. " * 20))
        public, private = self.replay()
        self.assertEqual(public["decisions"][0]["reason"], "stored_text_unverified_or_changed")
        self.assertEqual(public["provider_attempts"], 0)
        self.assertEqual(private["private_queue"], [])

    def test_wrong_fact_or_request_hash_cannot_deliver(self):
        def wrong_fact(payload):
            response = self.checker(payload)
            return {**response, "facts": {**trial.FACTS, "department_current_opinion": "unmodified_opinion"}}
        def stale_request(payload):
            return {**self.checker(payload), "request_sha256": "stale"}
        for check, reason in ((wrong_fact, "review_fact_mismatch"), (stale_request, "review_request_mismatch")):
            public, private = self.replay(check)
            self.assertEqual(public["decisions"][0]["reason"], reason)
            self.assertEqual(private["private_queue"], [])

    def test_timeout_is_held_without_retry(self):
        def timeout(payload):
            self.calls.append(payload)
            raise TimeoutError()
        public, _ = self.replay(timeout)
        self.assertEqual((public["provider_attempts"], len(self.calls), public["queue_count"]), (1, 1, 0))
        self.assertEqual(public["decisions"][0]["reason"], "provider_connection_error")

    def test_read_only_and_bounded_parameters(self):
        commands = []
        class Connection:
            def execute(self, query, params=None):
                commands.append((query, params))
                return self
            def fetchone(self):
                return ("on",)
            def fetchall(self):
                return []
        self.assertEqual(trial.read_batch(Connection()), [])
        self.assertEqual(commands[0][0], "SET TRANSACTION READ ONLY")
        self.assertEqual(commands[1][0], "SHOW transaction_read_only")
        self.assertEqual(commands[-1][1], ([trial.AUDIT, trial.BILL], trial.AS_OF))

    def test_read_only_requires_confirmation_and_row_cap(self):
        class Connection:
            mode = "off"
            def execute(self, _, params=None):
                return self
            def fetchone(self):
                return (self.mode,)
            def fetchall(self):
                return ["oversized"] * 7
        with self.assertRaisesRegex(RuntimeError, "database_read_only_unconfirmed"):
            trial.read_batch(Connection())
        connection = Connection()
        connection.mode = "on"
        with self.assertRaisesRegex(ValueError, "scope_limit_exceeded"):
            trial.read_batch(connection)


if __name__ == "__main__":
    unittest.main()
