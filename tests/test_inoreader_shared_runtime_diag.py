"""The diagnostic wrapper names the hold reason with fixed codes only (no network)."""
import contextlib
import io
import json
import unittest
import urllib.error

from collectors.inoreader import shared_runtime_diag as D
from collectors.source_registry.discovery.policy import Problem


def run(exc):
    def boom(argv):
        raise exc
    out = io.StringIO()
    with contextlib.redirect_stdout(out), self_assert_exit():
        D.main([], run=boom)
    return json.loads(out.getvalue().strip())


@contextlib.contextmanager
def self_assert_exit():
    try:
        yield
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError("no exit")


class Diag(unittest.TestCase):
    def test_problem_code_is_named(self):
        got = run(Problem("SHARED_QUOTA_EXHAUSTED"))
        self.assertEqual(got["status"], "HOLD")
        self.assertEqual(got["detail"], {"type": "Problem", "code": "SHARED_QUOTA_EXHAUSTED"})

    def test_free_text_and_urls_are_dropped(self):
        err = urllib.error.HTTPError("https://secret.example/v1/acquire?token=x", 409, "Conflict", {}, None)
        got = run(err)
        self.assertEqual(got["detail"]["http"], 409)
        self.assertNotIn("secret.example", json.dumps(got))

    def test_cause_is_described(self):
        try:
            try:
                raise Problem("STATE_LEASE_HELD")
            except Problem as inner:
                raise RuntimeError("wrapped with https://x.example") from inner
        except RuntimeError as outer:
            got = run(outer)
        self.assertEqual(got["detail"]["cause"], {"type": "Problem", "code": "STATE_LEASE_HELD"})
        self.assertNotIn("x.example", json.dumps(got))

    def test_success_passes_through(self):
        self.assertIsNone(D.main([], run=lambda argv: None))


if __name__ == "__main__":
    unittest.main()
