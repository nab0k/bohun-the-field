"""Offline PostgreSQL acceptance check for the D2RC route policy."""
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import psycopg

from collectors.kb_pilot import admit
from collectors.kb_pilot.free import FreeModel, OpenAICompatible
from collectors.kb_pilot.gemini import Gemini, Ledger

SCENARIOS = {
    "short": (429, {"Retry-After": "120"}, "rate_limited", 120),
    "unknown": (429, {}, "rate_limited", 900),
    "daily": (429, {"x-ratelimit-remaining-requests": "0",
                    "x-ratelimit-reset-requests": "2h3m4.5s"}, "daily_quota", 7384),
    "billing": (402, {}, "billing_blocked", None),
}


def main():
    scenario = os.environ["D2RC_SCENARIO"]
    mode = os.environ["D2RC_PHASE"]
    code, headers, reason, seconds = SCENARIOS[scenario]
    calls = 0

    def transport(url, _headers, body):
        nonlocal calls
        calls += 1
        if mode == "failure":
            raise HTTPError(url, code, "fixture", headers, None)
        packet = json.loads(body["messages"][1]["content"])
        verdicts = [{"n": sentence["n"], "ok": True} for sentence in packet["sentences"]]
        return {"choices": [{"message": {"content": json.dumps({
            "defence_relevant": True, "verdicts": verdicts,
        })}}]}

    model = FreeModel(Gemini("unused", Ledger()), providers=[
        OpenAICompatible("groq", "https://example.org/groq", "fixture", "fixture"),
    ], transport=transport)
    with psycopg.connect(host=os.environ["D2RC_SOCKET"], port=55432) as conn:
        if mode == "failure":
            conn.execute(Path("db/migrations/20261124_synthesis_review_route_block.sql").read_text())
            conn.execute("DELETE FROM knowledge.synthesis_review_retry")
        else:
            conn.execute("UPDATE knowledge.synthesis_review_retry SET next_attempt_at = now() - interval '1 second'")
            if mode == "restored":
                # Explicit test-only restoration of route availability.
                conn.execute("DELETE FROM knowledge.synthesis_review_route_block")
        conn.commit()
        with (patch.object(admit, "sources_text", return_value="fixture"),
              patch.object(admit, "in_feed", return_value=False)):
            result = admit.run(conn, model, limit=1, retry_queue=True, retry_policy=True)
        if mode == "restored":
            assert result["published_ids"] == [1] and calls == 1, (result, calls)
            assert conn.execute("SELECT count(*) FROM knowledge.synthesis_review_retry").fetchone() == (0,)
        else:
            assert result["no_reviewer"] == 1 and result["published"] == 0, result
            assert calls == int(mode == "failure"), (mode, calls)
            route = conn.execute("""
                SELECT reason, extract(epoch FROM resume_at - clock_timestamp())
                FROM knowledge.synthesis_review_route_block WHERE provider = 'groq'
            """).fetchone()
            assert route[0] == reason, route
            assert route[1] is None if seconds is None else seconds - 15 < route[1] <= seconds + 1, route
            assert conn.execute("""
                SELECT synthesis_id, version, reason FROM knowledge.synthesis_review_retry
            """).fetchall() == [(1, 1, reason)]
            assert conn.execute("""
                SELECT decision, reason_codes, public_release FROM knowledge.synthesis_admission
                WHERE synthesis_id = 1 AND version = 1
            """).fetchone() == ("HOLD", ["run_limit"], False)
    print(f"PASS {scenario} {mode}")


if __name__ == "__main__":
    main()
