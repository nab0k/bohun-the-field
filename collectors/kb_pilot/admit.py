"""Automatic admission of pilot syntheses to the feed (D-20261030-AUTO-ADMISSION): no owner step.

For each held pilot synthesis (HOLD owner_review, generator kb-pilot|…):
1. code checks: the parts the code wrote (context, policy direction, supply chain) come from verified rows; every
   number in the parts a model wrote (headline, what happened, bigger picture, why it matters) must be in the sources;
2. a second free model of another provider reads every model-written sentence against the sources;
3. self-correction: a rejected sentence is replaced by the reviewer's fix when the fix passes the number check and a
   second review; otherwise the sentence is dropped;
4. decision: PUBLISH when the reviewer finds the brief relevant to the defence or dual-use industry (the pilot does
   not filter news before research, D-20261029-EXP028-COST; the feed does), the headline stands, "what happened" and "why it matters" keep text, at most half of the
   sentences were dropped, the story is not already in the feed and the run's limit is not reached; otherwise HOLD
   with the reasons. A changed text becomes version 2; nothing is edited in place.
Free providers only; when none answers, the synthesis stays held and the next run retries it.
"""
import argparse
import json
import os
import re
import sys

from ..synthesis.contracts import canonical, sha
from .extract import verifier_excludes
from .pilot import numbers_in
from .publish import clean_sources, headline_of
from .prompts import REVIEW_SCHEMA, REVIEW_SYSTEM

RULES = "auto-admission-v1"
MODEL_SECTIONS = ("what_happened", "bigger_picture", "why_it_matters")
CODE_SECTIONS = ("context", "policy_direction", "supply_chain")
MAX_DROP_SHARE = 0.5
MAX_PUBLISH = 10
SOURCE_CHARS = 6000
SENTENCE = re.compile(r"(?<=[a-z0-9)%”\"'])[.!?]\s+(?=[A-Z“\"'])")


def sentences(text):
    out = []
    for line in (text or "").split("\n"):
        out += [s.strip() for s in SENTENCE.split(line.strip()) if s.strip()]
    return out


def numbers_ok(sentence, source_numbers):
    return all(n in source_numbers for n in numbers_in(sentence))


def review_items(version):
    """Numbered model-written sentences: (n, section, text); n = 0 is the headline."""
    items = [(0, "headline", version["headline"])]
    for section in MODEL_SECTIONS:
        for s in sentences(version.get(section)):
            items.append((len(items), section, s))
    return items


def review_packet(sources_text, version, items):
    database = "\n".join(version.get(s) or "" for s in CODE_SECTIONS if version.get(s))
    return {"sources": sources_text[:SOURCE_CHARS], "database": database[:3000],
            "sentences": [{"n": n, "section": sec, "text": t} for n, sec, t in items]}


def verdicts_of(answer):
    return {v.get("n"): v for v in (answer or {}).get("verdicts") or [] if isinstance(v, dict)}


def corrected(items, verdicts, source_numbers):
    """Sentences kept as they are, fixes to re-check, and n of the dropped ones."""
    keep, fixes, dropped = {}, {}, []
    for n, sec, text in items:
        v = verdicts.get(n)
        if v and v.get("ok") is True and numbers_ok(text, source_numbers):
            keep[n] = text
        elif v and (v.get("fix") or "").strip() and numbers_ok(v["fix"], source_numbers) and len(v["fix"]) >= 20:
            fixes[n] = v["fix"].strip()
        else:
            dropped.append(n)
    return keep, fixes, dropped


def rebuild(version, items, texts):
    """The version with only the accepted sentences (texts: n → sentence)."""
    out = dict(version)
    out["headline"] = headline_of(texts.get(0, ""))
    for section in MODEL_SECTIONS:
        out[section] = " ".join(texts[n] for n, sec, _ in items if sec == section and n in texts) or None
    out["why_it_matters"] = out["why_it_matters"] or ""
    out["sources"] = clean_sources(version.get("sources"))
    return out


def decide(version, items, texts, already_in_feed, published_so_far, max_publish=MAX_PUBLISH, relevant=True):
    reasons = [] if relevant else ["not_defence_relevant"]
    if not version.get("sources"):
        reasons.append("no_real_source")
    if not texts.get(0):
        reasons.append("headline_unsupported")
    if not version.get("what_happened"):
        reasons.append("what_happened_empty")
    if not version.get("why_it_matters"):
        reasons.append("why_it_matters_empty")
    if len(items) - len(texts) > MAX_DROP_SHARE * len(items):
        reasons.append("too_many_unsupported")
    if not (version.get("context") or version.get("supply_chain") or version.get("policy_direction")):
        reasons.append("no_background")
    if already_in_feed:
        reasons.append("story_already_in_feed")
    if not reasons and published_so_far >= max_publish:
        reasons.append("run_limit")
    return ("PUBLISH" if not reasons else "HOLD"), reasons


def review(cheap, sources_text, version, items):
    """Two review rounds with fixes; returns (texts n → sentence, provider, stats) or None if nobody answered."""
    source_numbers = set(numbers_in(sources_text + "\n" + "\n".join(version.get(s) or "" for s in CODE_SECTIONS)))
    exclude = verifier_excludes(cheap, None)
    first = cheap.free_many([cheap.request(REVIEW_SYSTEM, review_packet(sources_text, version, items), REVIEW_SCHEMA,
                                           max_output=4000, step="review")], exclude=[exclude])[0]
    if first[0] != "ok":
        return None
    relevant = (first[1] or {}).get("defence_relevant") is True
    keep, fixes, dropped = corrected(items, verdicts_of(first[1]), source_numbers)
    fixed = 0
    if fixes:
        again = [(n, sec, fixes[n]) for n, sec, _ in items if n in fixes]
        second = cheap.free_many([cheap.request(REVIEW_SYSTEM, review_packet(sources_text, version, again),
                                                REVIEW_SCHEMA, max_output=3000, step="review")], exclude=[exclude])[0]
        if second[0] == "ok":
            ok2 = verdicts_of(second[1])
            for n, _, text in again:
                if (ok2.get(n) or {}).get("ok") is True:
                    keep[n] = text
                    fixed += 1
                else:
                    dropped.append(n)
        else:
            dropped += list(fixes)
    return keep, first[2].get("model"), {"sentences": len(items), "fixed": fixed, "dropped": len(set(dropped)),
                                         "relevant": relevant}


def held(cur, limit):
    cur.execute("""SELECT s.id, s.event_id, v.version, v.headline, v.what_happened, v.context, v.policy_direction,
                          v.bigger_picture, v.supply_chain, v.why_it_matters, v.sources
                     FROM knowledge.synthesis s
                     JOIN knowledge.synthesis_version v ON v.synthesis_id = s.id AND v.version = s.active_version
                     JOIN knowledge.synthesis_admission a ON a.synthesis_id = s.id AND a.version = s.active_version
                    WHERE a.decision = 'HOLD' AND 'owner_review' = ANY(a.reason_codes) AND NOT s.is_hidden
                      AND v.generator LIKE 'kb-pilot|%%'
                 ORDER BY s.id DESC LIMIT %s""", (limit,))
    keys = ("headline", "what_happened", "context", "policy_direction", "bigger_picture", "supply_chain",
            "why_it_matters", "sources")
    return [{"synthesis_id": r[0], "event_id": r[1], "version": r[2], "v": dict(zip(keys, r[3:]))}
            for r in cur.fetchall()]


def sources_text(cur, event_id):
    cur.execute("""SELECT i.title, left(i.raw_data->'content_capture'->>'text', 3000)
                     FROM knowledge.event_document d JOIN intelligence.items i ON i.id = d.item_id
                    WHERE d.event_id = %s ORDER BY i.published_at, i.id""", (event_id,))
    return "\n\n".join((t or "") + "\n" + (b or "") for t, b in cur.fetchall())


def in_feed(cur, event_id):
    """True when an item of this event (or a reprint of it) is already behind a published synthesis."""
    cur.execute("""SELECT EXISTS (
                     SELECT 1 FROM knowledge.event_document d
                       JOIN knowledge.item_embedding e ON e.item_id = d.item_id
                       JOIN knowledge.item_embedding e2 ON e2.origin_item_id = e.origin_item_id
                       JOIN knowledge.event_document d2 ON d2.item_id = e2.item_id AND d2.event_id <> d.event_id
                       JOIN knowledge.synthesis s2 ON s2.event_id = d2.event_id AND NOT s2.is_hidden
                       JOIN knowledge.synthesis_admission a2 ON a2.synthesis_id = s2.id AND a2.version = s2.active_version
                      WHERE d.event_id = %s AND a2.decision = 'PUBLISH' AND a2.public_release)""", (event_id,))
    return cur.fetchone()[0]


def write(cur, row, new, decision, reasons, provider, run_ref):
    sid, old = row["synthesis_id"], row["version"]
    changed = any((new.get(k) or None) != (row["v"].get(k) or None) for k in ("headline", "sources") + MODEL_SECTIONS)
    version = old
    if changed and decision == "PUBLISH":  # a held text is not stored: only the decision and its reasons

        version = old + 1
        cur.execute("""INSERT INTO knowledge.synthesis_version (synthesis_id, version, headline, what_happened, context,
                         policy_direction, bigger_picture, supply_chain, why_it_matters, sources, content_hash, origin,
                         generator) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'machine',%s)""",
                    (sid, version, new["headline"], new["what_happened"], new["context"], new["policy_direction"],
                     new["bigger_picture"], new["supply_chain"], new["why_it_matters"],
                     json.dumps(new["sources"], ensure_ascii=False), sha(canonical(new)),
                     RULES + "|" + (provider or "") + "|" + (run_ref or "local")))
        cur.execute("""INSERT INTO knowledge.synthesis_admission (synthesis_id, version, decision, reason_codes,
                         rules_version, checker, public_release, published_at)
                       VALUES (%s,%s,%s,%s,%s,'auto-admission',%s, CASE WHEN %s THEN now() END)""",
                    (sid, version, decision, reasons, RULES, decision == "PUBLISH", decision == "PUBLISH"))
        cur.execute("UPDATE knowledge.synthesis SET active_version = %s WHERE id = %s", (version, sid))
    else:
        cur.execute("""UPDATE knowledge.synthesis_admission SET decision = %s, reason_codes = %s, rules_version = %s,
                         public_release = %s, published_at = CASE WHEN %s THEN now() END, decided_at = now()
                       WHERE synthesis_id = %s AND version = %s""",
                    (decision, reasons, RULES, decision == "PUBLISH", decision == "PUBLISH", sid, version))
    return version


def run(conn, cheap, limit=60, max_publish=MAX_PUBLISH, run_ref=None, dry_run=False):
    stats = {"rules": RULES, "held": 0, "published": 0, "kept_held": 0, "no_reviewer": 0, "fixed": 0, "dropped": 0,
             "reasons": {}, "published_ids": [], "dry_run": dry_run}
    with conn.cursor() as cur:
        rows = held(cur, limit)
    stats["held"] = len(rows)
    for row in rows:
        with conn.cursor() as cur:
            text = sources_text(cur, row["event_id"])
            items = review_items(row["v"])
            result = review(cheap, text, row["v"], items) if text else None
            if result is None:
                stats["no_reviewer"] += 1
                continue  # stays HOLD owner_review: the next run retries it
            texts, provider, rs = result
            stats["fixed"] += rs["fixed"]
            stats["dropped"] += rs["dropped"]
            new = rebuild(row["v"], items, texts)
            decision, reasons = decide(new, items, texts, in_feed(cur, row["event_id"]), stats["published"],
                                       max_publish, rs["relevant"])
            for r in reasons:
                stats["reasons"][r] = stats["reasons"].get(r, 0) + 1
            if not dry_run:
                write(cur, row, new, decision, reasons, provider, run_ref)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        if decision == "PUBLISH":
            stats["published"] += 1
            stats["published_ids"].append(row["synthesis_id"])
        else:
            stats["kept_held"] += 1
    return stats


def main(argv=None):
    from ..synthesis.live import connect
    from .free import FreeModel
    from .gemini import Gemini, Ledger
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--run-ref", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--no-dossier-write", action="store_true", help="dry run: decide and print, write nothing")
    args, _ = parser.parse_known_args(argv)  # the workflow passes the pilot's flags too
    cheap = FreeModel(Gemini(os.environ.get("GEMINI_API_KEY") or "unused", Ledger()))
    if not cheap.enabled:
        print("KB_ADMIT_RESULT " + canonical({"stop": "NO_FREE_PROVIDER"}))
        return 1
    max_publish = args.limit if args.limit and args.limit <= MAX_PUBLISH else MAX_PUBLISH
    with connect() as conn:
        stats = run(conn, cheap, max_publish=max_publish, run_ref=args.run_ref, dry_run=args.no_dossier_write)
    stats["free"] = cheap.stats
    print("KB_ADMIT_RESULT " + canonical(stats))
    return 0


if __name__ == "__main__":
    sys.exit(main())
