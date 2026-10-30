"""Every news item into the market model (EXP-028 DESIGN_v2_STEP3, D-20261030-EXP028-V2-STEP3).

A free model reads one first-source item and returns entities and relations, each relation with a verbatim quote.
The code keeps a relation only when the quote is in the text, its amount (with its scale) and date are in the quote
and its ends are known entities. A second free model of another provider then judges every kept relation against its
quote (v2, D-20261030-EXP028-EXTRACT-V2: v1 was right in 18 of 30 sampled relations): agreed relations become verified
knowledge.edge rows, rejected ones are dropped, unchecked ones (no second provider left) are written as candidates.
Ends become knowledge.node rows (existing ones by key or alias). Reprints are not read again. Free providers only:
when they are spent, the run stops and the next run continues from knowledge.item_extraction.
"""
import argparse
from collections import Counter
import json
import os
import re
import sys

from ..synthesis.contracts import canonical, sha
from .dossier import is_generic, node_key, valid_date
from .pages import amount_forms, norm
from .prompts import EXTRACT_SCHEMA, EXTRACT_SYSTEM, NODE_KINDS, RELS, VERIFY_SCHEMA, VERIFY_SYSTEM

EXTRACTOR = "kb-extract-v2"
MAX_TEXT = 8000
MIN_TEXT = 300
MIN_QUOTE = 15
CHUNK = 9
EXTRACT_LIMIT = 120
NO_DST = {"milestone", "price"}
SAME_FAMILY = {"programme": ("programme", "product"), "product": ("programme", "product")}
YEAR = re.compile(r"\b(19[5-9]\d|20\d\d)\b")
BILLION = re.compile(r"\d\s*(?:billion|bn|bln|mrd|milliarde|mld)", re.I)
MILLION = re.compile(r"\d\s*(?:million|mn|mln|mio|millionen|m\b)", re.I)
QUOTE_FOLD = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"',
                            "„": '"', "«": '"', "»": '"', "–": "-", "—": "-", "‑": "-",
                            "­": ""})


def fold(text):
    return norm((text or "").translate(QUOTE_FOLD)).strip(" .,;:")


def check_relation(rel, entities, text_folded, published=None):
    """None when the relation may be written, otherwise the reason it is dropped."""
    if rel.get("rel") not in RELS:
        return "bad_rel"
    quote = fold(rel.get("quote"))
    if len(quote) < MIN_QUOTE:
        return "no_quote"
    if quote not in text_folded:
        return "quote_not_in_text"
    for end in ("src", "dst"):
        name = (rel.get(end) or "").strip()
        if end == "dst" and (rel["rel"] in NO_DST or not name):
            continue
        if fold(name) not in entities:
            return "unknown_end"
        if is_generic(name):
            return "generic_name"
    if rel.get("amount") not in (None, "", 0):
        if not any(f in quote for f in amount_forms(rel["amount"])):
            return "amount_not_in_quote"
        if isinstance(rel["amount"], (int, float)) and (
                (BILLION.search(quote) and not MILLION.search(quote) and rel["amount"] < 1e8)
                or (MILLION.search(quote) and rel["amount"] < 1e5)):
            return "amount_scale"  # "$697.7 million" written as 697.7 (v1 sample)
    day = valid_date(rel.get("date"))
    if day:
        years = set(YEAR.findall(quote))
        if years and day[:4] not in years:
            return "date_not_in_quote"  # the article date for an "August 2025" fact (v1 sample)
        if not years and published and abs(int(day[:4]) - int(published[:4])) > 1:
            return "date_not_in_quote"
    if isinstance(rel.get("quantity"), (int, float)) and rel["quantity"] >= 10:
        q = rel["quantity"]
        forms = {str(int(q)), "{:,}".format(int(q)), "{:,}".format(int(q)).replace(",", ".")} if q == int(q) else {str(q)}
        if not any(f in quote for f in forms):
            return "quantity_not_in_quote"
    return None


def checked(answer, text, published=None):
    """Entities by folded name → kind, kept relations, Counter of drop reasons."""
    entities = {}
    for e in answer.get("entities") or []:
        if isinstance(e, dict) and e.get("kind") in NODE_KINDS and (e.get("name") or "").strip():
            entities.setdefault(fold(e["name"]), (e["kind"], e["name"].strip()))
    text_folded = fold(text)
    kept, dropped = [], Counter()
    for rel in answer.get("relations") or []:
        if not isinstance(rel, dict):
            dropped["not_object"] += 1
            continue
        reason = check_relation(rel, entities, text_folded, published)
        if reason:
            dropped[reason] += 1
        else:
            kept.append(rel)
    return entities, kept, dropped


def edge_row(rel, entities, item):
    """Plain values of one knowledge.edge row (node ids are resolved by the store)."""
    src = entities[fold(rel["src"])]
    dst = entities.get(fold(rel.get("dst") or "")) if rel["rel"] not in NO_DST else None
    day = valid_date(rel.get("date"))
    precision = rel.get("precision") if rel.get("precision") in ("day", "month", "year") else None
    if not day:
        years = sorted(set(YEAR.findall(rel["quote"])))
        # one year in the quote dates the fact; otherwise it stays undated, never the article date (v1 sample)
        day, precision = (years[0] + "-01-01", "year") if len(years) == 1 else (None, None)
    amount = rel.get("amount") if isinstance(rel.get("amount"), (int, float)) and rel["amount"] else None
    basis = [EXTRACTOR, rel["rel"], src[0], node_key(src[1]), dst and dst[0], dst and node_key(dst[1]), day,
             item["origin"]]
    return {"src": src, "dst": dst, "rel": rel["rel"], "occurred_on": day, "date_precision": precision,
            "amount": amount, "currency": (rel.get("currency") or "").upper()[:3] or None if amount else None,
            "quantity": rel.get("quantity") if isinstance(rel.get("quantity"), (int, float)) else None,
            "unit": (rel.get("unit") or None), "commitment": rel.get("commitment") or None,
            "polarity": "negative" if rel.get("negative") is True else "positive",
            "what": rel["quote"].strip()[:2000], "quote": rel["quote"].strip()[:1000],
            "row_hash": sha("x|" + json.dumps(basis, ensure_ascii=False)), "row_data": rel}


WEAK_JUDGES = {"cloudflare", "mistral"}  # gemma-4 (reasoning off) and PAYG-only Mistral: last resort as judges


def verifier_excludes(cheap, extractor_provider):
    """Never the provider that extracted; the weaker free models only when no stronger one is left."""
    names = {p.name for p in cheap.providers if p.name not in cheap.off} - {extractor_provider}
    return {extractor_provider} | (WEAK_JUDGES if names - WEAK_JUDGES else set())


def verify_packet(rows):
    return {"relations": [{"n": n, "src": r["src"][1], "rel": r["rel"], "dst": r["dst"][1] if r["dst"] else "",
                           "date": r["occurred_on"] or "", "amount": r["amount"], "currency": r["currency"],
                           "quantity": r["quantity"], "quote": r["quote"]} for n, r in enumerate(rows)]}


def apply_verdicts(rows, answer):
    """Rows the second model agreed with, and how many it rejected or left without a verdict."""
    ok = {v.get("n") for v in (answer or {}).get("verdicts") or [] if isinstance(v, dict) and v.get("ok") is True}
    said = {v.get("n") for v in (answer or {}).get("verdicts") or [] if isinstance(v, dict)}
    kept = [r for n, r in enumerate(rows) if n in ok]
    return kept, sum(1 for n in range(len(rows)) if n in said and n not in ok), \
        sum(1 for n in range(len(rows)) if n not in said)


def context_of(entities, rows):
    """The programme the item is about: first programme end of a kept relation, else first programme entity."""
    for r in rows:
        for end in (r["src"], r["dst"]):
            if end and end[0] == "programme" and not is_generic(end[1]):
                return end
    for kind, name in entities.values():
        if kind == "programme" and not is_generic(name):
            return kind, name
    return None


class Store:
    def __init__(self, cur):
        self.cur, self.cache = cur, {}

    def node(self, kind, name):
        key = node_key(name)
        if (kind, key) in self.cache:
            return self.cache[(kind, key)]
        kinds = list(SAME_FAMILY.get(kind, (kind,)))
        self.cur.execute("""SELECT id, status FROM knowledge.node WHERE kind = ANY(%s) AND (key = %s OR %s = ANY(aliases))
                             ORDER BY (kind = %s) DESC, (status = 'suspect'), id LIMIT 1""", (kinds, key, key, kind))
        r = self.cur.fetchone()
        if r and r[1] == "suspect":
            node_id = None
        elif r:
            node_id = r[0]
        else:
            self.cur.execute("""INSERT INTO knowledge.node (kind, key, name) VALUES (%s, %s, %s)
                                ON CONFLICT (kind, key) DO UPDATE SET updated_at = now() RETURNING id""",
                             (kind, key, name[:300]))
            node_id = self.cur.fetchone()[0]
        self.cache[(kind, key)] = node_id
        return node_id

    def write(self, item, rows, context, run_ref, status="verified"):
        ctx = self.node(*context) if context else None
        written = 0
        for r in rows:
            src = self.node(*r["src"])
            dst = self.node(*r["dst"]) if r["dst"] else None
            if src is None or (r["dst"] and dst is None):
                continue  # an end is a suspect node
            self.cur.execute("""INSERT INTO knowledge.edge (src_node_id, rel, dst_node_id, context_node_id, polarity,
                                       occurred_on, date_precision, amount, currency, quantity, unit, commitment, what,
                                       quote, quote_verified, source_url, item_id, origin_item_id, status, row_data,
                                       row_hash, extractor, run_ref)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true,%s,%s,%s,%s,%s,%s,%s,%s)
                                ON CONFLICT (context_node_id, row_hash) DO NOTHING""",
                             (src, r["rel"], dst, ctx, r["polarity"], r["occurred_on"], r["date_precision"], r["amount"],
                              r["currency"], r["quantity"], r["unit"], r["commitment"], r["what"], r["quote"],
                              item["url"], item["item_id"], item["origin"], status,
                              json.dumps(r["row_data"], ensure_ascii=False), r["row_hash"], EXTRACTOR, run_ref))
            written += self.cur.rowcount
        return written

    def log(self, item_id, outcome, provider=None, written=0, dropped=None, error=None, run_ref=None):
        self.cur.execute("""INSERT INTO knowledge.item_extraction (item_id, extractor, outcome, provider, written, dropped,
                                   error, run_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                            ON CONFLICT (item_id, extractor) DO UPDATE SET outcome = EXCLUDED.outcome,
                              provider = EXCLUDED.provider, written = EXCLUDED.written, dropped = EXCLUDED.dropped,
                              error = EXCLUDED.error, run_ref = EXCLUDED.run_ref, processed_at = now()""",
                         (item_id, EXTRACTOR, outcome, provider, written, json.dumps(dict(dropped or {})),
                          (error or "")[:300] or None, run_ref))


def pending(cur, day_from, day_to, limit):
    cur.execute("""SELECT i.id, i.title, i.url, i.raw_data->'content_capture'->>'text', i.published_at,
                          coalesce(e.origin_item_id, i.id), coalesce(e.origin_method, 'self')
                     FROM intelligence.items i
                LEFT JOIN knowledge.item_embedding e ON e.item_id = i.id
                LEFT JOIN knowledge.item_extraction x ON x.item_id = i.id AND x.extractor = %s
                    WHERE x.item_id IS NULL AND i.published_at >= %s::date AND i.published_at < %s::date + 1
                      AND length(i.raw_data->'content_capture'->>'text') >= %s
                 ORDER BY i.published_at, i.id LIMIT %s""", (EXTRACTOR, day_from, day_to, MIN_TEXT, limit))
    return [{"item_id": a, "title": t or "", "url": u, "text": ((t or "") + "\n\n" + (b or ""))[:MAX_TEXT],
             "published": p.isoformat(), "origin": o, "origin_method": m} for a, t, u, b, p, o, m in cur.fetchall()]


def run(conn, cheap, day_from, day_to, limit=1000, run_ref=None):
    stats = {"extractor": EXTRACTOR, "items": 0, "reprints": 0, "done": 0, "no_relations": 0, "errors": Counter(),
             "written": 0, "kept": 0, "verified": 0, "unchecked": 0, "dropped": Counter(), "by_provider": Counter(),
             "verifier": Counter(), "stop": None}
    with conn.cursor() as cur:
        todo = pending(cur, day_from, day_to, limit)
    stats["items"] = len(todo)
    store = Store(None)
    for i in range(0, len(todo), CHUNK):
        if not cheap.enabled:
            stats["stop"] = "FREE_PROVIDERS_SPENT"
            break
        chunk = todo[i:i + CHUNK]
        firsts = [it for it in chunk if it["origin_method"] == "self"]
        answers = cheap.free_many([cheap.request(EXTRACT_SYSTEM, {"date": it["published"][:10], "text": it["text"]},
                                                 EXTRACT_SCHEMA, max_output=4000, step="extract") for it in firsts])
        work = []  # (item, extractor provider, entities, rows, dropped)
        for it, a in zip(firsts, answers):
            if a[0] == "ok":
                entities, kept, dropped = checked(a[1], it["text"], it["published"])
                work.append((it, a[2].get("model"), entities, [edge_row(r, entities, it) for r in kept], dropped))
        to_check = [w for w in work if w[3]]
        verdicts = cheap.free_many([cheap.request(VERIFY_SYSTEM, verify_packet(w[3]), VERIFY_SCHEMA, max_output=2000,
                                                  step="verify") for w in to_check],
                                   exclude=[verifier_excludes(cheap, w[1]) for w in to_check])
        verdict_of = {id(w): v for w, v in zip(to_check, verdicts)}
        with conn.cursor() as cur:
            store.cur = cur
            for it in chunk:
                if it["origin_method"] != "self":
                    store.log(it["item_id"], "skipped_reprint", run_ref=run_ref)
                    stats["reprints"] += 1
            for it, a in zip(firsts, answers):
                if a[0] == "error":
                    if str(a[1]) == "no_free_provider":
                        continue  # not logged: the next run retries it
                    store.log(it["item_id"], "error", error=type(a[1]).__name__ + ":" + str(a[1]), run_ref=run_ref)
                    stats["errors"][str(a[1])[:40]] += 1
            for w in work:
                it, provider, entities, rows, dropped = w
                written, status = 0, "verified"
                v = verdict_of.get(id(w))
                if v and v[0] == "ok":
                    stats["verifier"][v[2].get("model")] += 1
                    rows, rejected, silent = apply_verdicts(rows, v[1])
                    dropped.update({"verifier_rejected": rejected, "verifier_silent": silent})
                elif v:
                    status = "candidate"  # no second provider answered: written, but not read by the pilot
                    stats["unchecked"] += len(rows)
                dropped = Counter({k: n for k, n in dropped.items() if n})
                if rows:
                    written = store.write(it, rows, context_of(entities, rows), run_ref, status)
                outcome = "done" if rows else "no_relations"
                store.log(it["item_id"], outcome, provider, written, dropped, run_ref=run_ref)
                stats[outcome] += 1
                stats["kept"] += len(rows)
                stats["verified"] += len(rows) if status == "verified" else 0
                stats["written"] += written
                stats["dropped"].update(dropped)
                stats["by_provider"][provider] += 1
        conn.commit()
    return stats


def main(argv=None):
    from ..synthesis.live import connect
    from .free import FreeModel
    from .gemini import Gemini, Ledger
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day-from", required=True)
    parser.add_argument("--day-to", required=True)
    # Free quotas are shared with the pilot (run 41091231587: extraction spent them, the pilot fell back to paid
    # Gemini): extraction runs after the pilot and admission and takes at most EXTRACT_LIMIT items per run.
    parser.add_argument("--limit", type=int, default=EXTRACT_LIMIT)
    parser.add_argument("--run-ref", default=os.environ.get("GITHUB_RUN_ID"))
    args, _ = parser.parse_known_args(argv)  # the workflow passes the pilot's flags too
    # Gemini only builds the request body for the free Gemini tier; paid Gemini is never called here.
    cheap = FreeModel(Gemini(os.environ.get("GEMINI_API_KEY") or "unused", Ledger()))
    if not cheap.enabled:
        print("KB_EXTRACT_RESULT " + canonical({"stop": "NO_FREE_PROVIDER"}))
        return 1
    with connect() as conn:
        stats = run(conn, cheap, args.day_from, args.day_to, args.limit or EXTRACT_LIMIT, args.run_ref)
    stats["free"] = cheap.stats
    print("KB_EXTRACT_RESULT " + canonical({k: (dict(v) if isinstance(v, Counter) else v) for k, v in stats.items()}))
    return 1 if stats["stop"] and stats["done"] + stats["no_relations"] == 0 else 0


if __name__ == "__main__":
    sys.exit(main())
