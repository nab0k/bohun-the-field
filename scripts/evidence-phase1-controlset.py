#!/usr/bin/env python3
"""Control set for independent (human) evaluation of Evidence Phase 1 — READ ONLY, free.

PRE-REGISTERED SELECTION (committed before the first run; do not change after seeing results):
  pool       intelligence.items already stored. No originals are fetched, nothing is written.
  news       item_type='news' with raw_data.visible_text (Inoreader body) of >= MIN_CHARS chars,
             English by a stop-word heuristic, not the PAC-3 article or any bohun_staging document.
  tenders    item_type='tender' (TED): the longest string field of the stored notice is used
             if >= MIN_CHARS chars; at most MAX_TED notices.
  dedup      a candidate is skipped if its 5-word-shingle Jaccard with an already selected text
             is >= 0.5 (syndicated copies) or its normalised title repeats.
  sampling   random.Random(SEED) shuffle inside each publisher, publishers in shuffled order,
             round-robin, at most MAX_PER_PUBLISHER per publisher, until TARGET items.
  frozen     the manifest stores ids, titles, URLs, text length and SHA-256 of the exact text used;
             texts themselves stay in Supabase (third-party content, public repo).

HOLD-OUT RULE: until human labels exist, the control set is used ONLY for descriptive statistics.
No rule, prompt or threshold may be tuned on it.

Free descriptive experiments (no LLM), reported separately for KNOWN texts (synthetic fixtures +
PAC-3, already seen during development) and NEW texts (the control set):
  E-A text quality: length, truncation markers, boilerplate markers.
  E-B guard cue rates per 1,000 words: raw regex vs the post-series proper-name filter.
  E-C syndication: duplicate titles and near-duplicate bodies across all stored news items.
"""
import collections
import hashlib
import json
import os
import random
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from intelligence.evidence_phase1 import documents as D  # noqa: E402
from intelligence.evidence_phase1.guards import (CANCEL_CUES, NEGATION_CUES, PLAN_CUES,  # noqa: E402
                                                 SPECULATIVE_CUES, _hits, looks_english)

SEED = 20261013
TARGET = 24
MAX_TED = 6
MAX_PER_PUBLISHER = 2
MIN_CHARS = 1200
JACCARD_DUP = 0.5
OUT = ROOT / "output/evidence-phase1"
TRUNCATION = re.compile(r"(read more|continue reading|\[…\]|\[\.\.\.\]|…\s*$|the post .{0,200} appeared first on)", re.I)
BOILERPLATE = re.compile(r"(cookie|subscribe|newsletter|sign up|all rights reserved|privacy policy)", re.I)


def notice(title, payload):
    msg = json.dumps(payload, ensure_ascii=False, default=str).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::notice title={title}::{msg}")


def connect():
    import psycopg
    conn = psycopg.connect(
        host=os.getenv("SUPABASE_DB_HOST", "aws-1-eu-central-1.pooler.supabase.com"),
        port=int(os.getenv("SUPABASE_DB_PORT", "5432")), dbname="postgres",
        user=os.getenv("SUPABASE_DB_USER", "postgres.tecrucawzukxqtchpxrh"),
        password=os.environ["SUPABASE_DB_PASSWORD"], sslmode="require", connect_timeout=20)
    conn.read_only = True
    return conn


def shingles(text, k=5):
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(w[i:i + k]) for i in range(max(0, len(w) - k + 1))}


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def longest_string(obj):
    best = ("", "")
    stack = [("", obj)]
    while stack:
        path, v = stack.pop()
        if isinstance(v, dict):
            stack += [(f"{path}.{k}" if path else k, x) for k, x in v.items()]
        elif isinstance(v, list):
            stack += [(f"{path}[{i}]", x) for i, x in enumerate(v)]
        elif isinstance(v, str) and len(v) > len(best[1]):
            best = (path, v)
    return best


def cue_rates(texts):
    words = sum(len(t.split()) for t in texts) or 1
    out = {}
    for name, pat in (("PLAN", PLAN_CUES), ("NEGATION", NEGATION_CUES), ("CANCEL", CANCEL_CUES), ("SPECULATIVE", SPECULATIVE_CUES)):
        raw = sum(len(re.findall(pat, t, flags=re.I)) for t in texts)
        filt = sum(len(_hits(pat, t)) for t in texts)
        out[name] = {"raw_per_1k_words": round(1000 * raw / words, 2), "filtered_per_1k_words": round(1000 * filt / words, 2),
                     "removed_by_proper_name_filter": raw - filt}
    out["words"] = words
    return out


def quality(texts):
    lens = [len(t) for t in texts]
    return {"n": len(texts), "chars_median": int(statistics.median(lens)) if lens else 0,
            "chars_min": min(lens) if lens else 0, "chars_max": max(lens) if lens else 0,
            "with_truncation_marker": sum(bool(TRUNCATION.search(t[-400:])) for t in texts),
            "with_boilerplate_marker": sum(bool(BOILERPLATE.search(t)) for t in texts)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"seed": SEED, "target": TARGET, "min_chars": MIN_CHARS, "errors": []}
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SHOW transaction_read_only")
        if cur.fetchone()[0] != "on":
            raise RuntimeError("NOT_READ_ONLY")
        cur.execute("SELECT now()")
        report["snapshot_at"] = cur.fetchone()[0].isoformat()
        cur.execute("""SELECT i.id, i.item_type, i.external_id, i.title, i.url, i.published_at, i.raw_data, s.name
                       FROM intelligence.items i LEFT JOIN intelligence.sources s ON s.id = i.source_id""")
        rows = cur.fetchall()
        cur.execute("SELECT final_url, original_url FROM bohun_staging.inoreader_documents")
        staged = cur.fetchall()
        staged_urls = {u for r in staged for u in r if u}

    inv = collections.Counter()
    status = collections.Counter()
    news, tenders = [], []
    for item_id, itype, ext, title, url, pub, raw, src in rows:
        raw = raw or {}
        inv[itype] += 1
        if itype == "news":
            text = raw.get("visible_text") or ""
            status[raw.get("content_status", "NO_CONTENT_STATUS_FIELD")] += 1
            publisher = raw.get("origin_title") or src or "unknown"
            field = "raw_data.visible_text"
        else:
            field, text = longest_string(raw)
            field = "raw_data." + field
            publisher = "TED"
        news_or_ted = news if itype == "news" else tenders
        news_or_ted.append({"item_id": item_id, "item_type": itype, "external_id": ext, "title": title, "url": url,
                            "published_at": pub.isoformat() if pub else None, "publisher": publisher,
                            "text_field": field, "text": text})

    lengths = [len(x["text"]) for x in news]
    report["inventory"] = {
        "items_by_type": dict(inv), "news_content_status": dict(status),
        "news_with_text_ge_300": sum(l >= 300 for l in lengths),
        "news_with_text_ge_min_chars": sum(l >= MIN_CHARS for l in lengths),
        "tenders_with_text_ge_min_chars": sum(len(x["text"]) >= MIN_CHARS for x in tenders),
        "tender_text_fields": dict(collections.Counter(x["text_field"] for x in tenders).most_common(5)),
        "staging_documents": len(staged),
    }

    def eligible(x):
        if len(x["text"]) < MIN_CHARS:
            return "SHORT"
        if x["url"] in staged_urls or "pac-3-edge" in (x["url"] or ""):
            return "KNOWN_EXCLUDED"
        if x["item_type"] == "news" and looks_english(x["text"]) is not True:
            return "NOT_ENGLISH"
        return None

    reasons = collections.Counter(eligible(x) or "ELIGIBLE" for x in news + tenders)
    report["eligibility"] = dict(reasons)

    rng = random.Random(SEED)
    pool = collections.defaultdict(list)
    for x in news + tenders:
        if not eligible(x):
            pool[x["publisher"]].append(x)
    publishers = sorted(pool)
    rng.shuffle(publishers)
    for p in publishers:
        rng.shuffle(pool[p])
    chosen, chosen_sh, titles, skipped_dup = [], [], set(), 0
    taken = collections.Counter()
    progress = True
    while len(chosen) < TARGET and progress:
        progress = False
        for p in publishers:
            if len(chosen) >= TARGET:
                break
            if taken[p] >= (MAX_TED if p == "TED" else MAX_PER_PUBLISHER):
                continue
            while pool[p]:
                x = pool[p].pop(0)
                sh = shingles(x["text"])
                if norm_title(x["title"]) in titles or any(jaccard(sh, o) >= JACCARD_DUP for o in chosen_sh):
                    skipped_dup += 1
                    continue
                chosen.append(x)
                chosen_sh.append(sh)
                titles.add(norm_title(x["title"]))
                taken[p] += 1
                progress = True
                break
    manifest = [{"rank": i + 1, "item_id": x["item_id"], "item_type": x["item_type"], "external_id": x["external_id"],
                 "publisher": x["publisher"], "title": (x["title"] or "")[:200], "url": x["url"],
                 "published_at": x["published_at"], "text_field": x["text_field"], "text_chars": len(x["text"]),
                 "text_sha256": hashlib.sha256(x["text"].encode()).hexdigest()} for i, x in enumerate(chosen)]
    report["selection"] = {"selected": len(manifest), "skipped_near_duplicates": skipped_dup,
                           "per_publisher": dict(taken), "target_met": len(manifest) >= 20}

    # ---- free descriptive experiments: KNOWN vs NEW ----
    known = [d["text"] for d in D.load_fixtures(["syn-contract-negation", "syn-cancelled-dates"])]
    try:
        pac = D.load_staging(limit=1, title_like="PAC-3")[0]["text"][:12000]
        known.append(pac)
    except Exception as exc:
        report["errors"].append("PAC3_READ_FAILED:" + type(exc).__name__)
    new = [x["text"] for x in chosen]
    report["E-A_text_quality"] = {"known": quality(known), "new": quality(new)}
    report["E-B_guard_cue_rates"] = {"known": cue_rates(known), "new": cue_rates(new),
                                     "note": "descriptive only: cue frequency, not accuracy"}
    bodies = [x for x in news if len(x["text"]) >= 300]
    title_dups = sum(c - 1 for c in collections.Counter(norm_title(x["title"]) for x in news).values() if c > 1)
    sh = [shingles(x["text"]) for x in bodies]
    near = sum(1 for i in range(len(sh)) for j in range(i + 1, len(sh)) if jaccard(sh[i], sh[j]) >= JACCARD_DUP)
    report["E-C_syndication"] = {"news_items": len(news), "duplicate_titles_beyond_first": title_dups,
                                 "bodies_compared": len(bodies), "near_duplicate_body_pairs_jaccard_ge_0_5": near}

    (OUT / "controlset-manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False))
    (OUT / "controlset-report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str))
    notice("controlset-report", report)
    for i in range(0, len(manifest), 6):
        notice(f"controlset-manifest-{i // 6 + 1}", manifest[i:i + 6])
    return 0


if __name__ == "__main__":
    sys.exit(main())
