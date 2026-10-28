"""Manual live stages of the synthesis route: item_processing migration, doctrine load, control-live.

Runs only inside the reviewed manual workflow. The public workflow log receives counts, codes and
cost only: no source text, quotes or synthesis prose. Synthesis rows are written with
public_release=false; stage A keeps every would-be PUBLISH as HOLD owner_review (rules v2.0 §10).
"""
import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

from . import PIPELINE_VERSION
from .contracts import Document, Resources, canonical, sha
from .route import run

ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT / "resources"
MIGRATION = ROOT / "sql" / "item_processing_v14.sql"
LIVE_VERSION = PIPELINE_VERSION.replace("offline", "live")

MODEL = "gemini-3.5-flash-lite"
PRICE_VERSION = "gemini35fl-standard-input26-v1"
MAX_OUTPUT_TOKENS = 2000
CAP_MICRO_USD = 1_000_000
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"


class BudgetStop(RuntimeError):
    """Stops the whole run; nothing is written after a stop."""


def micro_usd(input_tokens, output_tokens):
    # Same Standard price as intelligence_pipeline.costing: $0.30/M input, $2.50/M output incl. thoughts.
    return (input_tokens * 3 + output_tokens * 25 + 9) // 10


@dataclass
class Ledger:
    cap: int = CAP_MICRO_USD
    spent: int = 0
    calls: int = 0

    def reserve(self, body_bytes):
        # A token is never shorter than one UTF-8 byte, so bytes bound input tokens from above.
        worst = micro_usd(body_bytes, MAX_OUTPUT_TOKENS)
        if self.spent + worst > self.cap:
            raise BudgetStop("BUDGET_CAP_WOULD_BE_EXCEEDED")
        return worst

    def charge(self, usage):
        if not isinstance(usage, dict):
            raise BudgetStop("UNKNOWN_COST_NO_USAGE")
        inp, out = usage.get("promptTokenCount"), usage.get("candidatesTokenCount", 0)
        thoughts = usage.get("thoughtsTokenCount", 0)
        if any(type(n) is not int or n < 0 for n in (inp, out, thoughts)):
            raise BudgetStop("UNKNOWN_COST_INVALID_USAGE")
        cost = micro_usd(inp, out + thoughts)
        self.spent += cost; self.calls += 1
        if self.spent > self.cap:
            raise BudgetStop("BUDGET_CAP_REACHED")
        return cost


def http_post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={**headers, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as reply:
        return json.load(reply)


class GeminiWriter:
    """One structured call per event; cost from usageMetadata, unknown cost stops the run."""

    def __init__(self, key, ledger, transport=http_post):
        if not key:
            raise BudgetStop("GEMINI_API_KEY_ABSENT")
        self.key, self.ledger, self.transport = key, ledger, transport

    def body(self, req):
        system = req["system_instruction"] + "\n\nReturn only JSON matching this schema:\n" + canonical(req["schema"])
        return {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": canonical(req["packet"])}]}],
                "generationConfig": {"temperature": req["temperature"], "maxOutputTokens": MAX_OUTPUT_TOKENS,
                                     "responseMimeType": "application/json",
                                     "thinkingConfig": {"thinkingBudget": 0}}}

    def __call__(self, req):
        body = self.body(req)
        self.ledger.reserve(len(canonical(body).encode()))
        try:
            reply = self.transport(GEMINI_URL.format(MODEL), {"x-goog-api-key": self.key}, body)
        except urllib.error.HTTPError as exc:
            raise BudgetStop("MODEL_HTTP_%d_NO_RETRY" % exc.code) from None
        except Exception:
            # The call may have been billed; the cost is unknown, so the run stops.
            raise BudgetStop("MODEL_CALL_UNCERTAIN_NO_RETRY") from None
        self.ledger.charge(reply.get("usageMetadata"))
        candidates = reply.get("candidates") or []
        if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
            raise ValueError("MODEL_INCOMPLETE_OUTPUT")
        parts = candidates[0].get("content", {}).get("parts", [])
        return json.loads("".join(p.get("text", "") for p in parts if not p.get("thought")))


# --- database ----------------------------------------------------------------

def connect():
    import hashlib
    import psycopg
    from psycopg.conninfo import make_conninfo
    password = os.environ.get("SUPABASE_DB_PASSWORD")
    if not password:
        raise SystemExit("DATABASE_SECRET_ABSENT")
    ca = (ROOT.parent / "company_ner" / "supabase-prod-ca-2021.crt").resolve()
    if hashlib.sha256(ca.read_bytes()).hexdigest() != "700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7":
        raise SystemExit("DATABASE_CA_PIN_MISMATCH")
    return psycopg.connect(make_conninfo(
        host="aws-1-eu-central-1.pooler.supabase.com", port=5432, dbname="postgres",
        user="postgres.tecrucawzukxqtchpxrh", password=password,
        sslmode="verify-full", sslrootcert=str(ca), connect_timeout=20))


def load_documents(cur, control):
    """A-06 control items from intelligence.items; text = title + newline + captured body."""
    ids = [d["id"] for d in control["documents"]]
    cur.execute("""SELECT i.id, i.external_id, i.title, i.url, i.published_at, i.raw_data
                     FROM intelligence.items i WHERE i.external_id = ANY(%s)""", (ids,))
    rows = {r[1]: r for r in cur.fetchall()}
    docs, item_ids, report = [], {}, {"found": 0, "missing": [], "no_body": [], "text_pin_match": 0}
    for ref in control["documents"]:
        row = rows.get(ref["id"])
        if row is None:
            report["missing"].append(ref["id"][-6:]); continue
        item_id, external_id, title, url, published, raw = row
        if published is None:
            report["no_body"].append(external_id[-6:]); continue
        body = ((raw or {}).get("content_capture") or {}).get("text")
        if not body:
            report["no_body"].append(external_id[-6:]); continue
        text = title + "\n" + body
        report["found"] += 1
        report["text_pin_match"] += int(sha(text) == ref["text_sha256"])
        outlet = (raw or {}).get("origin_title") or url
        docs.append(Document(external_id, title, text, url or ref["url"],
                             published.isoformat(), outlet))
        item_ids[external_id] = item_id
    return docs, item_ids, report


MIN_BODY_CHARS = 300
MAX_WINDOW_ITEMS = 200


def window_bounds(day_from, day_to):
    from datetime import date, timedelta
    a, b = date.fromisoformat(day_from), date.fromisoformat(day_to)
    if not timedelta(0) <= b - a <= timedelta(days=2):
        raise SystemExit("DAY_WINDOW_MUST_BE_1_TO_3_DAYS")
    return a.isoformat(), (b + timedelta(days=1)).isoformat()


def load_window(cur, day_from, day_to, limit=MAX_WINDOW_ITEMS):
    """Unprocessed items published in the window with a captured body of at least MIN_BODY_CHARS."""
    start, end = window_bounds(day_from, day_to)
    cur.execute("""SELECT i.id, i.external_id, i.title, i.url, i.published_at, i.raw_data
                     FROM intelligence.items i
                    WHERE i.published_at >= %s::date AND i.published_at < %s::date
                      AND length(i.raw_data->'content_capture'->>'text') >= %s
                      AND NOT EXISTS (SELECT 1 FROM knowledge.item_processing p
                                       WHERE p.item_id = i.id AND p.pipeline_version = %s)
                    ORDER BY i.published_at, i.id LIMIT %s""", (start, end, MIN_BODY_CHARS, LIVE_VERSION, limit))
    docs, item_ids, report = [], {}, {"found": 0, "skipped": 0}
    for item_id, external_id, title, url, published, raw in cur.fetchall():
        body = raw["content_capture"]["text"]
        try:
            docs.append(Document(external_id, title, title + "\n" + body, url or "", published.isoformat(),
                                 raw.get("origin_title") or url or "unknown"))
        except ValueError:  # e.g. a non-https URL; such an item is not routed
            report["skipped"] += 1; continue
        item_ids[external_id] = item_id
        report["found"] += 1
    return docs, item_ids, report


def doctrine_snapshot(cur):
    """Doctrine rows as source-bound context facts for the policy_direction block."""
    cur.execute("""SELECT d.id, t.slug, d.jurisdiction, d.title, d.document_date, d.direction, d.source_url
                     FROM knowledge.doctrine d
                     JOIN knowledge.doctrine_topic dt ON dt.doctrine_id = d.id
                     JOIN knowledge.topic t ON t.id = dt.topic_id""")
    rows = []
    for did, slug, jur, title, day, direction, url in cur.fetchall():
        text = "%s, %s (%s): %s" % (jur, title, day.isoformat(), direction)
        rows.append({"id": "D%d" % did, "topic": slug, "origin": "source_fact", "kind": "doctrine",
                     "date": day.isoformat() + "T00:00:00+00:00", "text": text, "quote": direction,
                     "source_text": direction, "source_url": url, "modality": "announced"})
    return {"policy_direction": rows}


def already_processed(cur, item_ids):
    cur.execute("SELECT count(*) FROM knowledge.item_processing WHERE item_id = ANY(%s) AND pipeline_version = %s",
                (list(item_ids), LIVE_VERSION))
    return cur.fetchone()[0]


def seed_topics(cur, topics):
    for t in topics["topics"]:
        cur.execute("""INSERT INTO knowledge.topic (slug, title, description) VALUES (%s,%s,%s)
                       ON CONFLICT (slug) DO NOTHING""", (t["slug"], t["title"], t.get("description")))


def db_modality(fact):
    """dictionaries-v2 negation_storage: negated -> predicate not_*, modality announced."""
    if fact["modality"] == "negated":
        return "not_" + fact["predicate"], "announced"
    return fact["predicate"], fact["modality"]


def first_amount(fact):
    for a in fact.get("amounts", []):
        if len(a["currency"]) == 3 and a["currency"].isalpha():
            return a["value"], a["currency"]
    return None, None


def write_event(cur, event, item_ids, extractor_version):
    """One event inside the caller's transaction. Returns the item_processing outcome."""
    decision = event["admission"]["decision"]
    codes = list(event["admission"]["reason_codes"])
    sources = event["packet"]["sources"]
    if decision == "REJECT" or event["type"] == "other" or any(s["class"] not in {"P0", "P1", "P2"} for s in sources):
        return None, ("reject" if decision == "REJECT" else "hold"), codes
    facts = [f for f in event["packet"]["facts"] if f["id"].startswith("F")]
    db_fact = {}
    for f in facts:
        predicate, modality = db_modality(f)
        if f["modality"] == "negated" and not f["modality_hits"].get("negated"):
            raise ValueError("NEGATION_WITHOUT_MARKER")
        amount, currency = first_amount(f)
        cur.execute("""INSERT INTO knowledge.fact (item_id, artifact_sha256, predicate, subject_name, object_text, amount,
                         currency, modality, commitment, quote, quote_sha256, quote_verified, extractor_version)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (item_id, quote_sha256, predicate, extractor_version)
                       DO UPDATE SET quote_verified = EXCLUDED.quote_verified RETURNING id""",
                    (item_ids[f["document_id"]], f["artifact_sha256"], predicate, f["subject_name"],
                     f["object_text"], amount, currency, modality, f["commitment"], f["quote"],
                     f["quote_sha256"], f["quote_verified"], extractor_version))
        db_fact[f["id"]] = cur.fetchone()[0]
    first = event["packet"]["event"]
    ref = event.get("procedure_ref")
    link = "procedure_id" if ref else "single_document" if len(event["document_ids"]) == 1 else "hard_features"
    title = event.get("title") or event["document_ids"][0]
    cur.execute("""INSERT INTO knowledge.event (event_type, title, procedure_ref, link_method, first_published_at)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id""", (event["type"], title[:500], ref, link, first["first_published_at"]))
    event_id = cur.fetchone()[0]
    for n, t in enumerate(event["topic"]["all"]):
        cur.execute("""INSERT INTO knowledge.event_topic (event_id, topic_id, is_main)
                       SELECT %s, id, %s FROM knowledge.topic WHERE slug = %s""", (event_id, n == 0, t["slug"]))
    for s in sources:
        cur.execute("""INSERT INTO knowledge.event_document (event_id, item_id, relation, is_primary_source, source_class)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (event_id, item_ids[s["document_id"]], s["role"], s["role"] == "primary", s["class"]))
    for fid in db_fact.values():
        cur.execute("INSERT INTO knowledge.event_fact (event_id, fact_id) VALUES (%s,%s)", (event_id, fid))
    out = event.get("writer_output")
    if out and out.get("why_it_matters") and event.get("text_checks"):
        if decision == "PUBLISH":
            # Stage A: owner reviews every first synthesis before any public_release (rules v2.0 §10).
            decision, codes = "HOLD", ["owner_review"]
        join = lambda block: " ".join(x["text"] for x in out[block]) or None
        version = {"headline": out["headline"]["text"], "what_happened": join("what_happened"),
                   "context": join("context"), "policy_direction": join("policy_direction"),
                   "bigger_picture": join("bigger_picture"), "supply_chain": join("supply_chain"),
                   "why_it_matters": join("why_it_matters")}
        source_list = [{k: s[k] for k in ("url", "outlet", "date", "role", "class")} for s in
                       sorted(sources, key=lambda s: s["role"] != "primary")][:5]
        cur.execute("""INSERT INTO knowledge.synthesis (event_id, active_version, active_origin)
                       VALUES (%s, 1, 'machine') RETURNING id""", (event_id,))
        synthesis_id = cur.fetchone()[0]
        cur.execute("""INSERT INTO knowledge.synthesis_version (synthesis_id, version, headline, what_happened, context,
                         policy_direction, bigger_picture, supply_chain, why_it_matters, sources, content_hash, origin, generator)
                       VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'machine',%s)""",
                    (synthesis_id, version["headline"], version["what_happened"], version["context"],
                     version["policy_direction"], version["bigger_picture"], version["supply_chain"],
                     version["why_it_matters"], json.dumps(source_list), sha(canonical(version)),
                     MODEL + "|" + LIVE_VERSION))
        claims = [("headline", out["headline"])] + [(b, x) for b in
                  ("what_happened", "context", "policy_direction", "bigger_picture", "supply_chain", "why_it_matters")
                  for x in out[b]]
        checks = {(c["section"], c["claim_text"]): c for c in event["text_checks"]["claims"]}
        for n, (section, claim) in enumerate(claims, 1):
            check = checks.get((section, claim["text"]), {"check_status": "mismatch", "check_codes": ["not_checked"]})
            cur.execute("""INSERT INTO knowledge.synthesis_claim (synthesis_id, version, claim_no, section, claim_text,
                             check_status, check_codes) VALUES (%s,1,%s,%s,%s,%s,%s)""",
                        (synthesis_id, n, section, claim["text"], check["check_status"], check["check_codes"]))
            for fid in claim["fact_ids"]:
                if fid in db_fact:
                    cur.execute("""INSERT INTO knowledge.synthesis_claim_fact (synthesis_id, version, claim_no, fact_id)
                                   VALUES (%s,1,%s,%s) ON CONFLICT DO NOTHING""", (synthesis_id, n, db_fact[fid]))
        cur.execute("""INSERT INTO knowledge.synthesis_admission (synthesis_id, version, decision, reason_codes,
                         rules_version, checker, public_release) VALUES (%s,1,%s,%s,%s,'auto-admission',false)""",
                    (synthesis_id, decision, sorted(set(codes)), event["admission"]["rules_version"]))
    return event_id, "hold" if decision != "PUBLISH" else "event", sorted(set(codes))


def mark_items(cur, event, item_ids, event_id, outcome, codes, cost):
    for n, doc_id in enumerate(event["document_ids"]):
        cur.execute("""INSERT INTO knowledge.item_processing (item_id, pipeline_version, outcome, reason_codes, event_id, cost_micro_usd)
                       VALUES (%s,%s,%s,%s,%s,%s)""",
                    (item_ids[doc_id], LIVE_VERSION, outcome, codes, event_id, cost if n == 0 else 0))


# --- stages ------------------------------------------------------------------

def stage_migration(conn, apply):
    sql = MIGRATION.read_text(encoding="utf-8")
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("SELECT to_regclass('knowledge.item_processing') IS NOT NULL")
            ok = cur.fetchone()[0]
        if not apply:
            raise _Rollback(ok)  # leaving the block by exception rolls the transaction back
    return {"stage": "migration-apply", "table_present": ok}


class _Rollback(Exception):
    def __init__(self, ok):
        super().__init__("TRIAL_ROLLBACK"); self.ok = ok


def stage_doctrine(conn, resources):
    data = json.loads((RESOURCES / "doctrine_v1_verified.json").read_text(encoding="utf-8"))
    inserted = 0
    with conn.transaction():
        with conn.cursor() as cur:
            seed_topics(cur, resources.topics)
            for r in data["records"]:
                if r.get("verified") is not True:
                    continue
                cur.execute("""INSERT INTO knowledge.doctrine (jurisdiction, title, document_date, direction, change_summary, source_url)
                               VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (jurisdiction, title, document_date) DO NOTHING RETURNING id""",
                            (r["jurisdiction"], r["title"], r["document_date"], r["direction"], r["change_summary"], r["source_url"]))
                row = cur.fetchone()
                if row is None:
                    continue
                inserted += 1
                for slug in r["topics"]:
                    cur.execute("""INSERT INTO knowledge.doctrine_topic (doctrine_id, topic_id)
                                   SELECT %s, id FROM knowledge.topic WHERE slug = %s""", (row[0], slug))
    return {"stage": "doctrine-load", "inserted": inserted}


def stage_control_live(conn, resources, key, transport=http_post):
    control = json.loads((RESOURCES / "a06_control_items.json").read_text(encoding="utf-8"))
    with conn.cursor() as cur:
        docs, item_ids, report = load_documents(cur, control)
        if already_processed(cur, item_ids.values()):
            return {"stage": "control-live", "stop": "ALREADY_PROCESSED", "inputs": report}
    return route_and_write(conn, resources, key, transport, "control-live", docs, item_ids, report)


def stage_day_live(conn, resources, key, day_from, day_to, transport=http_post):
    with conn.cursor() as cur:
        docs, item_ids, report = load_window(cur, day_from, day_to)
    if not docs:
        return {"stage": "day-live", "stop": "NO_UNPROCESSED_ITEMS_WITH_BODY", "inputs": report}
    return route_and_write(conn, resources, key, transport, "day-live", docs, item_ids, report)


def route_and_write(conn, resources, key, transport, stage, docs, item_ids, report):
    with conn.cursor() as cur:
        context = doctrine_snapshot(cur)
    ledger = Ledger()
    writer = GeminiWriter(key, ledger, transport)
    costs = {}

    def counted(req):
        before = ledger.spent
        try:
            return writer(req)
        finally:
            costs[req["packet_hash"]] = ledger.spent - before

    try:
        result = run(docs, resources, context=context, writer=counted)
    except BudgetStop as stop:
        return {"stage": stage, "stop": str(stop), "spent_micro_usd": ledger.spent,
                "model_calls": ledger.calls, "db_writes": 0, "inputs": report}
    summary = []
    with conn.transaction():
        with conn.cursor() as cur:
            seed_topics(cur, resources.topics)
            for event in result["events"]:
                cost = costs.get(sha(canonical(event["packet"])), 0)
                try:
                    with conn.transaction():  # savepoint: one event, all or nothing
                        event_id, outcome, codes = write_event(cur, event, item_ids, LIVE_VERSION)
                        mark_items(cur, event, item_ids, event_id, outcome, codes, cost)
                except Exception as exc:  # the failed event is rolled back alone and marked as error
                    outcome, codes = "error", ["write_error:" + type(exc).__name__]
                    with conn.transaction():
                        mark_items(cur, event, item_ids, None, outcome, codes, cost)
                summary.append({"docs": len(event["document_ids"]), "type": event["type"],
                                "topic": (event["topic"]["main"] or {}).get("slug"), "outcome": outcome,
                                "codes": codes, "synthesis": bool(event.get("writer_output")), "cost_micro_usd": cost})
            for doc_id in result["duplicate_document_ids"]:
                # Exact reprints (same URL and text) are marked too, so the next run skips them.
                cur.execute("""INSERT INTO knowledge.item_processing (item_id, pipeline_version, outcome, reason_codes)
                               VALUES (%s,%s,'hold',%s)""", (item_ids[doc_id], LIVE_VERSION, ["exact_reprint"]))
    return {"stage": stage, "model": MODEL, "price_version": PRICE_VERSION,
            "spent_micro_usd": ledger.spent, "model_calls": ledger.calls, "inputs": report,
            "events": summary, "public_release": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True,
                        choices=["migration-trial", "migration-apply", "doctrine-load", "control-live", "day-live"])
    parser.add_argument("--day-from")
    parser.add_argument("--day-to")
    args = parser.parse_args(argv)
    resources = Resources.load(RESOURCES / "topics_v3.json", RESOURCES / "dictionaries_v3.json",
                               RESOURCES / "writer_prompt_v2.md")
    with connect() as conn:
        if args.stage.startswith("migration"):
            try:
                out = stage_migration(conn, args.stage == "migration-apply")
            except _Rollback as rb:
                out = {"stage": "migration-trial", "table_present_before_rollback": rb.ok, "rolled_back": True}
        elif args.stage == "doctrine-load":
            out = stage_doctrine(conn, resources)
        elif args.stage == "control-live":
            out = stage_control_live(conn, resources, os.environ.get("GEMINI_API_KEY"))
        else:
            out = stage_day_live(conn, resources, os.environ.get("GEMINI_API_KEY"), args.day_from, args.day_to)
    # Public log: counts, codes and cost only.
    print("SYNTHESIS_LIVE_RESULT " + canonical(out))
    return 1 if out.get("stop") and out["stop"] not in {"ALREADY_PROCESSED", "NO_UNPROCESSED_ITEMS_WITH_BODY"} else 0


if __name__ == "__main__":
    sys.exit(main())
