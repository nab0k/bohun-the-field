#!/usr/bin/env python3
"""One-item Inoreader -> original source -> Gemini -> HOLD staging pilot."""
import datetime as dt
import hashlib
import ipaddress
import json
import os
import socket
import sys
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors/inoreader"))
from content_contract import extract_inoreader_content, visible_text  # noqa: E402
import sync as inoreader  # noqa: E402
from intelligence.gemini_evidence_validator import validate_candidate  # noqa: E402

OUT = ROOT / "output/inoreader-gemini-pilot"
KEY = OUT / "pilot-key.json"
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
USER_AGENT = "Bohundefence-Evidence-Pilot/1.0"


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def stable_id(prefix, *parts):
    return prefix + ":" + sha256("\0".join(map(str, parts)).encode())[:24]


def connect():
    return psycopg.connect(
        host=os.getenv("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
        port=int(os.getenv("SUPABASE_DB_PORT", "5432")),
        dbname="postgres",
        user=os.getenv("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
        password=os.environ["SUPABASE_DB_PASSWORD"],
        sslmode="require",
        connect_timeout=20,
    )


def assert_public_web_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError("UNSAFE_ORIGINAL_URL")
    for result in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM):
        address = ipaddress.ip_address(result[4][0])
        if not address.is_global:
            raise RuntimeError("NON_PUBLIC_ORIGINAL_HOST")


def fetch_original(url):
    assert_public_web_url(url)
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    robots = urllib.robotparser.RobotFileParser()
    robots.set_url(robots_url)
    try:
        robots.read()
    except (OSError, urllib.error.URLError, urllib.error.HTTPError):
        raise RuntimeError("ROBOTS_POLICY_UNAVAILABLE") from None
    if not robots.can_fetch(USER_AGENT, url):
        raise RuntimeError("ROBOTS_DISALLOWS_FETCH")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urllib.request.urlopen(request, timeout=30) as response:
        final_url = response.geturl()
        assert_public_web_url(final_url)
        media_type = (response.headers.get_content_type() or "").lower()
        if media_type not in {"text/html", "application/xhtml+xml"}:
            raise RuntimeError("ORIGINAL_NOT_HTML")
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise RuntimeError("ORIGINAL_TOO_LARGE")
        charset = response.headers.get_content_charset() or "utf-8"
    html = raw.decode(charset, errors="replace")
    text = visible_text(html)
    if len(text) < 300:
        raise RuntimeError("ORIGINAL_TEXT_TOO_SHORT")
    return final_url, media_type, html, text


def choose_one_item():
    token = inoreader.access_token()
    subscriptions = inoreader.api("subscription/list", token).get("subscriptions", [])
    folders, _ = inoreader.folders_from_subscriptions(subscriptions)
    if not folders:
        raise RuntimeError("NO_BOHUN_FOLDERS")
    # One bounded Stream Contents request. Up to 10 candidates may be inspected;
    # exactly one item is selected and no second page is requested.
    folder = folders[0]
    stream = "user/-/label/" + folder
    result = inoreader.api(
        "stream/contents/" + urllib.parse.quote(stream, safe="/"), token, {"n": 10}
    )
    for raw_item in result.get("items", []):
        normalized = inoreader.normalize(raw_item, folder)
        content = normalized["raw_data"]
        if normalized.get("url") and content["content_status"] != "METADATA_ONLY":
            return raw_item, normalized
    raise RuntimeError("NO_BODY_ITEM_IN_BOUNDED_RESPONSE")


def call_gemini(evidence_id, source_url, evidence_text):
    prompt = {
        "instruction": (
            "Return exactly one conservative candidate claim supported by one exact contiguous span "
            "from evidence_text. Do not infer missing facts. Keep qa_status HOLD. Return JSON only."
        ),
        "required_shape": {
            "qa_status": "HOLD",
            "candidate_claims": [{
                "claim_id": "candidate:<stable-short-id>",
                "subject": "string",
                "predicate": "string",
                "object": {"value": "string", "datatype": "string", "unit": None},
                "evidence_ids": [evidence_id],
                "exact_spans": ["exact contiguous source span"],
                "confidence": 0.0,
            }],
            "candidate_event": {"type": "source_update", "claim_ids": ["same claim_id"]},
            "uncertainties": [],
        },
        "source_url": source_url,
        "evidence_id": evidence_id,
        "evidence_text": evidence_text,
    }
    request_body = json.dumps(
        {
            "contents": [{"parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "maxOutputTokens": 500,
                "temperature": 0,
            },
        }
    ).encode()
    request = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        data=request_body,
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    raw = result["candidates"][0]["content"]["parts"][0]["text"]
    return prompt, request_body, raw, json.loads(raw), result.get("usageMetadata")


def ensure_schema(cur):
    cur.execute("CREATE SCHEMA IF NOT EXISTS bohun_staging")
    cur.execute("REVOKE ALL ON SCHEMA bohun_staging FROM PUBLIC")
    cur.execute("REVOKE ALL ON SCHEMA bohun_staging FROM anon, authenticated")
    cur.execute(
        """CREATE TABLE IF NOT EXISTS bohun_staging.inoreader_documents (
          document_id text PRIMARY KEY,
          inoreader_id text NOT NULL,
          folder text NOT NULL,
          title text NOT NULL,
          original_url text NOT NULL,
          final_url text NOT NULL,
          published_at timestamptz,
          inoreader_summary_html text NOT NULL,
          inoreader_content_sha256 char(64) NOT NULL,
          original_raw_html text NOT NULL,
          original_content_sha256 char(64) NOT NULL,
          original_visible_text text NOT NULL,
          rights_status text NOT NULL DEFAULT 'UNKNOWN',
          qa_status text NOT NULL CHECK (qa_status='HOLD'),
          public_release boolean NOT NULL CHECK (public_release=false),
          fetched_at timestamptz NOT NULL,
          UNIQUE (inoreader_id, original_content_sha256)
        )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS bohun_staging.model_candidates (
          model_run_id text PRIMARY KEY,
          document_id text NOT NULL REFERENCES bohun_staging.inoreader_documents(document_id),
          model text NOT NULL,
          request_sha256 char(64) NOT NULL,
          response_json jsonb NOT NULL,
          validation_json jsonb NOT NULL,
          usage_json jsonb,
          qa_status text NOT NULL CHECK (qa_status='HOLD'),
          public_release boolean NOT NULL CHECK (public_release=false),
          created_at timestamptz NOT NULL,
          UNIQUE (document_id, model, request_sha256)
        )"""
    )
    cur.execute("REVOKE ALL ON ALL TABLES IN SCHEMA bohun_staging FROM PUBLIC")
    cur.execute("REVOKE ALL ON ALL TABLES IN SCHEMA bohun_staging FROM anon, authenticated")


def run():
    OUT.mkdir(parents=True, exist_ok=True)
    raw_item, item = choose_one_item()
    content = extract_inoreader_content(raw_item)
    final_url, media_type, original_html, original_text = fetch_original(item["url"])
    original_sha = sha256(original_html.encode())
    document_id = stable_id("document", "inoreader", item["external_id"], original_sha)
    evidence_text = original_text[:12000]
    evidence_id = stable_id("evidence", document_id, sha256(evidence_text.encode()))
    packet = {"evidence": [{"evidence_id": evidence_id, "exact_excerpt": evidence_text}]}
    prompt, request_body, raw_response, candidate, usage = call_gemini(
        evidence_id, final_url, evidence_text
    )
    if len(candidate.get("candidate_claims") or []) != 1:
        raise RuntimeError("GEMINI_DID_NOT_RETURN_EXACTLY_ONE_CLAIM")
    validation = validate_candidate(packet, candidate)
    model_run_id = stable_id("model-run", document_id, MODEL, sha256(request_body))
    now = dt.datetime.now(dt.timezone.utc)
    published = item.get("published_at")
    with connect() as db:
        with db.cursor() as cur:
            ensure_schema(cur)
            cur.execute(
                """INSERT INTO bohun_staging.inoreader_documents
                (document_id,inoreader_id,folder,title,original_url,final_url,published_at,
                 inoreader_summary_html,inoreader_content_sha256,original_raw_html,
                 original_content_sha256,original_visible_text,rights_status,qa_status,
                 public_release,fetched_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'UNKNOWN','HOLD',false,%s)
                ON CONFLICT (document_id) DO NOTHING""",
                (
                    document_id, item["external_id"], item["raw_data"]["folder"], item["title"],
                    item["url"], final_url, published, content["inoreader_summary_html"],
                    content["content_sha256"], original_html,
                    original_sha, original_text, now,
                ),
            )
            cur.execute(
                """INSERT INTO bohun_staging.model_candidates
                (model_run_id,document_id,model,request_sha256,response_json,validation_json,
                 usage_json,qa_status,public_release,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'HOLD',false,%s)
                ON CONFLICT (model_run_id) DO NOTHING""",
                (
                    model_run_id, document_id, MODEL, sha256(request_body), Jsonb(candidate),
                    Jsonb(validation), Jsonb(usage) if usage else None, now,
                ),
            )
    key = {
        "document_id": document_id,
        "model_run_id": model_run_id,
        "original_content_sha256": original_sha,
        "evidence_id": evidence_id,
        "provider_calls": {"inoreader_stream": 1, "gemini": 1},
    }
    KEY.write_text(json.dumps(key, indent=2))
    print(json.dumps({
        "stage": "INOREADER_GEMINI_STAGING_WRITE", "pass": True,
        "document_id": document_id, "model_run_id": model_run_id,
        "media_type": media_type, "exact_spans_verified": validation["exact_spans_verified"],
        "qa_status": "HOLD", "public_release": False,
        "provider_calls": key["provider_calls"], "production_tables_touched": 0,
    }))


def readback():
    key = json.loads(KEY.read_text())
    with connect() as db:
        db.execute("SET default_transaction_read_only=on")
        with db.cursor() as cur:
            cur.execute(
                """SELECT d.original_raw_html,d.original_content_sha256,d.original_visible_text,
                          d.qa_status,d.public_release,c.response_json,c.validation_json,
                          c.qa_status,c.public_release
                   FROM bohun_staging.inoreader_documents d
                   JOIN bohun_staging.model_candidates c ON c.document_id=d.document_id
                   WHERE d.document_id=%s AND c.model_run_id=%s""",
                (key["document_id"], key["model_run_id"]),
            )
            row = cur.fetchone()
            if not row:
                raise RuntimeError("PILOT_ROWS_NOT_FOUND")
            raw_html, stored_sha, original_text, d_qa, d_public, candidate, validation, c_qa, c_public = row
            if sha256(raw_html.encode()) != stored_sha.strip() or stored_sha.strip() != key["original_content_sha256"]:
                raise RuntimeError("ORIGINAL_HASH_MISMATCH")
            packet = {"evidence": [{"evidence_id": key["evidence_id"], "exact_excerpt": original_text[:12000]}]}
            repeated = validate_candidate(packet, candidate)
            if validation != repeated or d_qa != "HOLD" or c_qa != "HOLD" or d_public or c_public:
                raise RuntimeError("READBACK_GATE_FAILED")
            cur.execute(
                """SELECT COUNT(*) FROM information_schema.role_table_grants
                   WHERE table_schema='bohun_staging' AND grantee IN ('PUBLIC','anon','authenticated')"""
            )
            public_grants = cur.fetchone()[0]
            if public_grants:
                raise RuntimeError("PUBLIC_STAGING_GRANTS_FOUND")
    result = {
        "stage": "INDEPENDENT_INOREADER_GEMINI_READBACK", "pass": True,
        "document_id": key["document_id"], "model_run_id": key["model_run_id"],
        "original_hash_verified": True,
        "exact_spans_verified": repeated["exact_spans_verified"],
        "qa_status": "HOLD", "public_release": False,
        "public_role_grants": public_grants, "production_tables_touched": 0,
    }
    (OUT / "readback.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"run", "readback"}:
        raise SystemExit("usage: inoreader-gemini-staging-pilot.py run|readback")
    {"run": run, "readback": readback}[sys.argv[1]]()
