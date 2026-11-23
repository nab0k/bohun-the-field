import hashlib
import json
import os
from urllib.request import HTTPRedirectHandler, Request, build_opener
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlsplit

import psycopg

from collectors.kb_pilot import admit
from collectors.kb_pilot.free import FreeModel, OpenAICompatible
from collectors.kb_pilot.gemini import Gemini, Ledger

phase = os.environ["D2RC_PHASE"]

def provider(name):
    return OpenAICompatible(name, name, "fixture-key", name)

def transport(route, headers, request):
    if route in {"short_limit", "daily_limit", "billing"}:
        code = 402 if route == "billing" else 429
        raise HTTPError(route, code, "fixture", {"Retry-After": "60"}, None)
    if route == "invalid":
        return {"choices": [{"message": {"content": "{}"}}]}
    assert route == "recovered"
    packet = json.loads(request["messages"][1]["content"])
    answer = {"defence_relevant": True, "verdicts": [
        {"n": sentence["n"], "ok": True} for sentence in packet["sentences"]
    ]}
    return {"choices": [{"message": {"content": json.dumps(answer)}}]}

routes = ["short_limit", "daily_limit", "billing", "invalid"] if phase == "outage" else ["recovered"]
model = FreeModel(Gemini("unused", Ledger()), providers=[provider(name) for name in routes],
                  transport=transport)

def reviewed_cases(raw, digest):
    if len(raw.encode()) > 12_000 or hashlib.sha256(raw.encode()).hexdigest() != digest:
        raise SystemExit("Reviewed public fixtures SHA-256 mismatch or size exceeded")
    cases = json.loads(raw)
    fields = {"url", "evidence", "headline", "what_happened", "context", "why_it_matters"}
    if not isinstance(cases, list) or len(cases) != 2:
        raise SystemExit("Expected exactly two reviewed public fixtures")
    for case in cases:
        if not isinstance(case, dict) or case.keys() != fields or not all(
            isinstance(value, str) and value.strip() for value in case.values()
        ):
            raise SystemExit("Invalid public fixture")
        url = urlsplit(case["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise SystemExit("Expected a public HTTPS source URL")
        if len(case["evidence"]) > admit.SOURCE_CHARS:
            raise SystemExit("Evidence exceeds the review packet limit")
    return cases


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "openai/gpt-oss-20b"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_groq(url, headers, body, timeout):
    request = Request(url, data=json.dumps(body).encode(), method="POST",
                      headers={**headers, "Content-Type": "application/json",
                               "User-Agent": "bohun-kb-pilot/1.0"})
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        return json.load(response)


def cloud_recovery(conn):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise SystemExit("Groq key missing")
    if "groq" in admit.WEAK_JUDGES:
        raise SystemExit("Groq is excluded by the admission rules")
    cases = reviewed_cases(os.environ["D2RC_PUBLIC_FIXTURES"], os.environ["D2RC_FIXTURES_SHA256"])
    for sid, case in zip((1, 3), cases):
        conn.execute("""UPDATE knowledge.synthesis_version
                           SET headline = %s, what_happened = %s, context = %s, why_it_matters = %s,
                               sources = %s::jsonb
                         WHERE synthesis_id = %s AND version = 1""",
                     (case["headline"], case["what_happened"], case["context"], case["why_it_matters"],
                      json.dumps([{"url": case["url"]}]), sid))
    conn.commit()
    calls = attempts = 0

    def once(url, headers, body):
        nonlocal calls, attempts
        attempts += 1
        if calls:
            raise RuntimeError("probe_request_limit")
        calls += 1
        return post_groq(url, headers, body, timeout=35)

    reviewer = FreeModel(Gemini("unused", Ledger()),
                         providers=[OpenAICompatible("groq", GROQ_URL, key, GROQ_MODEL)],
                         transport=once)
    baseline_review = admit.review

    def bounded_review(*args):
        result = baseline_review(*args)
        return result if attempts == 1 else None

    rows = admit.held(conn.cursor(), 2, retry_queue=True)
    order = [[row["synthesis_id"], row["version"]] for row in rows]
    if order != [[1, 1], [3, 1]]:
        raise SystemExit("Unexpected retry order")
    with (patch.object(admit, "sources_text", return_value=cases[0]["evidence"]),
          patch.object(admit, "review", side_effect=bounded_review),
          patch.object(admit, "in_feed", return_value=False)):
        result = admit.run(conn, reviewer, limit=1, retry_queue=True)
    releases, = conn.execute("SELECT count(*) FROM knowledge.synthesis_admission WHERE public_release").fetchone()
    if calls != 1 or releases != result["published"] or releases > 1:
        raise SystemExit("Unexpected request or release count")
    status = ("correction_deferred" if attempts > 1 else
              "no_usable_review" if result["no_reviewer"] else
              "test_publish" if releases else "hold")
    print(json.dumps({
        "sha": os.environ.get("GITHUB_SHA", "local"),
        "fixtures_sha256": os.environ["D2RC_FIXTURES_SHA256"],
        "order": order, "requests": calls, "route": "groq", "model": GROQ_MODEL,
        "response_schema_accepted": reviewer.stats["free_ok"] == 1,
        "verdict": status, "reasons": result["reasons"], "test_releases": releases,
    }, sort_keys=True))


with psycopg.connect(host=os.environ["D2RC_SOCKET"], port=55432, dbname=os.environ.get("PGDATABASE", "postgres")) as conn:
    if phase == "outage":
        conn.execute("""
            CREATE SCHEMA knowledge;
            CREATE TABLE knowledge.synthesis (
                id integer PRIMARY KEY, event_id integer, active_version integer, is_hidden boolean
            );
            CREATE TABLE knowledge.synthesis_version (
                synthesis_id integer, version integer, headline text, what_happened text, context text,
                policy_direction text, bigger_picture text, supply_chain text, why_it_matters text,
                sources jsonb, context_sources jsonb, generator text, content_hash text, origin text
            );
            CREATE TABLE knowledge.synthesis_admission (
                synthesis_id integer, version integer, decision text, reason_codes text[],
                rules_version text, checker text, public_release boolean DEFAULT false,
                decided_at timestamptz DEFAULT now(), published_at timestamptz,
                PRIMARY KEY (synthesis_id, version)
            );
            INSERT INTO knowledge.synthesis
            SELECT id, id, 1, id = 90 FROM unnest(ARRAY[1, 2, 3, 90, 91, 92]) AS id;
            INSERT INTO knowledge.synthesis_version
            SELECT id, 1, 'Equipment delivery', 'The company delivered equipment.',
                   'A prior contract was signed.', NULL, NULL, NULL, 'The delivery improves supply.',
                   '[{"url":"https://example.org/release"}]', NULL, 'kb-pilot|fixture', NULL, NULL
              FROM knowledge.synthesis;
        """)

        for role in ("anon", "authenticated"):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                conn.execute(f"CREATE ROLE {role}")
        conn.execute(Path("db/migrations/20261123_synthesis_review_retry.sql").read_text())
        conn.execute("""
            INSERT INTO knowledge.synthesis_admission
                (synthesis_id, version, decision, reason_codes)
            VALUES (1, 1, 'HOLD', ARRAY['run_limit']), (2, 1, 'HOLD', ARRAY['owner_review']),
                   (90, 1, 'HOLD', ARRAY['run_limit']), (91, 1, 'HOLD', ARRAY['headline_unsupported']),
                   (92, 1, 'REJECT', ARRAY['run_limit'])
        """)
        conn.commit()
        with (patch.object(admit, "sources_text", side_effect=lambda _, event: "fixture" if event == 1 else ""),
              patch.object(admit, "in_feed", return_value=False)):
            result = admit.run(conn, model, retry_queue=True)
        assert result["no_reviewer"] == 2 and result["published"] == 0, result
        assert len(model.stats["fallback"]) == 4, model.stats
        assert conn.execute("""
            SELECT synthesis_id, version, reason, next_attempt_at > clock_timestamp()
            FROM knowledge.synthesis_review_retry ORDER BY synthesis_id
        """).fetchall() == [(1, 1, "reviewer_unavailable", True), (2, 1, "missing_evidence", True)]
        assert admit.held(conn.cursor(), 60, retry_queue=True) == []
        assert conn.execute("SELECT count(*) FROM knowledge.synthesis_admission WHERE public_release").fetchone() == (0,)
    else:
        conn.execute("""
            INSERT INTO knowledge.synthesis_admission (synthesis_id, version, decision, reason_codes)
            VALUES (3, 1, 'HOLD', ARRAY['owner_review'])
        """)
        conn.execute("""
            UPDATE knowledge.synthesis_review_retry SET next_attempt_at = now() - interval '1 second'
            WHERE synthesis_id = 1
        """)
        conn.commit()
        if phase == "cloud":
            cloud_recovery(conn)
            raise SystemExit(0)
        rows = admit.held(conn.cursor(), 60, retry_queue=True)
        assert [row["synthesis_id"] for row in rows] == [1, 3], rows
        with (patch.object(admit, "sources_text", return_value="fixture"),
              patch.object(admit, "in_feed", return_value=False)):
            dry = admit.run(conn, model, limit=1, dry_run=True, retry_queue=True)
            assert dry["published_ids"] == [1], dry
            assert conn.execute("SELECT count(*) FROM knowledge.synthesis_review_retry").fetchone() == (2,)
            result = admit.run(conn, model, limit=1, retry_queue=True)
            assert result["published_ids"] == [1], result
            with patch.object(admit, "held", return_value=rows[:1]):
                replay = admit.run(conn, model, limit=1, retry_queue=True)
            assert replay["published"] == 0, replay
        assert conn.execute("""
            SELECT synthesis_id, version FROM knowledge.synthesis_admission WHERE public_release
        """).fetchall() == [(1, 1)]
        assert [row["synthesis_id"] for row in admit.held(conn.cursor(), 60, retry_queue=True)] == [3]
print(f"PASS {phase}")
