"""Exact source audits on a temporary SQLite snapshot and stand-in HTTP."""
import datetime as dt
import io
import json
import sys
import unittest
from contextlib import nullcontext, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import admission as A  # noqa: E402
import observer as O  # noqa: E402
import run_daily as R  # noqa: E402
import search_cycle as C  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)
SINCE = (NOW - dt.timedelta(days=4)).isoformat()


class AuditQueue(unittest.TestCase):
    def setUp(self):
        self.conn = A.open_db(":memory:")
        self.addCleanup(self.conn.close)
        self.addCleanup(A.set_base, set(A.BASE["company"]), set(A.BASE["rnd"]))

    def channel(self, name, confirmed=False, status="accepted"):
        url = f"https://publisher.example/{name}/feed"
        self.conn.execute("INSERT INTO source_state VALUES (?,?,?,?,?,?,?,?,?)",
                          (name, url, status, "media", "feed", "PL", "en", SINCE, A.RULES_VERSION))
        if confirmed:
            self.conn.execute(
                "INSERT INTO admission_decision(candidate_url,channel_id,decision,reasons_json,record_json,"
                "rules_version,decided_at) VALUES (?,?,'accept','[]',?,?,?)",
                (url, name, '{"judge":{"by":"fixture"}}', A.RULES_VERSION, SINCE))
        return url

    def test_six_stalled_channels_use_audit_quota(self):
        for n in range(6):
            self.channel(f"stalled-{n}")
        self.channel("confirmed", confirmed=True)
        self.channel("on-hold", status="on_hold")
        audit = R.channel_audits(self.conn, limit=6)
        self.assertEqual([channel for _, channel in audit], [f"stalled-{n}" for n in range(6)])
        self.assertEqual([channel for _, channel in R.channel_audits(self.conn, limit=2)], ["stalled-0", "stalled-1"])
        selected = R.channel_audits(self.conn, ["on-hold", "absent", "confirmed"])
        self.assertEqual([channel for _, channel in selected], ["on-hold", "confirmed"])
        self.assertEqual([c["url"] for c, _ in selected],
                         ["https://publisher.example/on-hold/feed", "https://publisher.example/confirmed/feed"])

    def test_priority_observation_precedes_search_and_reports_a_limit(self):
        self.channel("a")
        self.channel("b")
        audit = R.channel_audits(self.conn)
        audit_map = {c["decision_url"]: ch for c, ch in audit}
        calls = []

        def observe(c, *args, **kwargs):
            calls.append(c["expected_channel_id"])
            return {"candidate": c, "fetch": {"status": 503}}

        def search(job):
            calls.append("search")
            return {"results": []}

        with patch.object(O, "observe", side_effect=observe):
            C.execute([{"layer": "types", "query": "fixture"}], search, None, self.conn, set(),
                      audit=audit_map, priority=[c for c, _ in audit])
        self.assertEqual(calls, ["a", "b", "search"])
        with patch.object(O, "observe", side_effect=O.Problem("REQUEST_LIMIT")):
            _, _, _, trace, observed, _, results = C.execute(
                [{"layer": "types", "query": "fixture"}], search, None, self.conn, set(),
                audit=audit_map, priority=[c for c, _ in audit])
        self.assertEqual(calls, ["a", "b", "search"])
        self.assertEqual(R.audit_outcomes(audit, observed, results, trace["stop"]),
                         {ch: {"state": "deferred", "reason": "REQUEST_LIMIT"} for ch in ("a", "b")})

    def test_transient_results_preserve_only_a_confirmed_grant(self):
        self.channel("confirmed", confirmed=True)
        missing_url = self.channel("stalled")
        self.conn.execute("INSERT INTO candidate_seen VALUES (?,?,?,?,?)",
                          ("https://publisher.example/", "accept", SINCE, 3, "stalled"))
        audit = R.channel_audits(self.conn, ["confirmed", "stalled"])
        observed = [{"candidate": c, "fetch": {"status": 503}} for c, _ in audit]
        _, decisions = A.admit(self.conn, observed, NOW - dt.timedelta(hours=8),
                               audit={c["decision_url"]: ch for c, ch in audit})
        self.assertEqual(R.audit_outcomes(audit, observed, decisions, None), {
            "confirmed": {"state": "checked", "decision": "retry", "reasons": ["transient_error"]},
            "stalled": {"state": "checked", "decision": "hold", "reasons": ["audit_transient", "transient_error"]},
        })
        self.assertEqual(dict(self.conn.execute("SELECT channel_id,status FROM source_state")),
                         {"confirmed": "accepted", "stalled": "on_hold"})
        self.assertEqual(list(self.conn.execute("SELECT decision FROM admission_decision WHERE channel_id='confirmed'")),
                         [("accept",)])
        self.assertEqual([c["url"] for c in R.recheck_candidates(self.conn)], [missing_url])

    def test_addressed_cli_reads_only_and_skips_global_work(self):
        self.channel("stalled")
        output = io.StringIO()
        pg = type("Connection", (), {})()
        with (
            patch.object(R.pg_sync, "connect", return_value=nullcontext(pg)),
            patch.object(R.pg_sync, "load", return_value=(self.conn, {"company": set(), "rnd": set()})) as load,
            patch.object(R.pg_sync, "save") as save,
            patch.object(R.H, "update") as health,
            patch.object(C, "network_parts") as network,
            redirect_stdout(output),
        ):
            R.main(["--dry-run", "--audit-channel", "stalled", "--audit-channel", "absent"])
        self.assertTrue(pg.read_only)
        self.assertTrue(load.call_args.kwargs["registry_only"])
        self.assertEqual(json.loads(output.getvalue())["audit"], {"selected": 1, "unavailable": 1})
        network.assert_not_called()
        health.assert_not_called()
        save.assert_not_called()

    def test_addressed_observation_is_scoped_and_apply_is_explicit(self):
        self.channel("stalled")
        self.channel("unselected")
        connection = object()
        ctx = {"company": set(), "rnd": set()}
        args = type("Args", (), {"audit_channel": ["stalled"], "dry_run": False,
                                "audit_judge": False, "apply_audit": False})()
        with (
            patch.object(R.pg_sync, "load", return_value=(self.conn, ctx)),
            patch.object(R.pg_sync, "save") as save,
            patch.object(C, "network_parts", return_value=(lambda limits: limits, lambda *a, **k: None)),
            patch.object(O, "observe", side_effect=lambda c, *a, **k: {"candidate": c, "fetch": {"status": 503}}) as observe,
        ):
            outcomes = R.run_addressed_audit(args, connection, None, NOW)
            self.assertEqual(set(outcomes), {"stalled"})
            self.assertEqual(observe.call_args.args[0]["expected_channel_id"], "stalled")
            save.assert_not_called()
            args.apply_audit = True
            R.run_addressed_audit(args, connection, None, NOW)
            run = save.call_args.args[3]
            self.assertEqual(run["audited_channels"], ["stalled"])
            self.assertEqual((run["done_jobs"], run["measure"]), ([], {}))


if __name__ == "__main__":
    unittest.main()
