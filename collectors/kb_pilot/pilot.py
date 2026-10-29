"""EXP-028 pilot run: the events of a day window → dossier rows, documents and syntheses for review.

Steps per DESIGN §8 v1.1: (1) group duplicates and get search keys (Gemini); (2) our archive first
(intelligence.items); (3) research with Google Search (Gemini, grounded); (4) code reads every cited page and
checks dates and amounts, official HTML becomes Markdown with anchors; (5) writer on verified facts only;
(6) code checks numbers and references, then the §6 rule: no chronology and no supply chain/doctrine → HOLD.
Nothing is written to the database. The public log gets counts and cost only.
"""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import sys

from . import PILOT_VERSION
from .gemini import Gemini, Ledger
from .pages import amount_forms, check_row, fetch_all, host, is_official, norm, sections
from .prompts import (GROUP_SCHEMA, GROUP_SYSTEM, RESEARCH_SCHEMA, RESEARCH_SYSTEM, SECTIONS, WRITER_SCHEMA,
                      WRITER_SYSTEM)
from ..synthesis.contracts import canonical, sha
from ..synthesis.live import BudgetStop, MODEL, PRICE_VERSION

ROOT = Path(__file__).resolve().parent
DOCTRINE = ROOT / "resources" / "doctrine_v2.json"
MAX_EVENT_CHARS = 5000
MAX_URLS_PER_EVENT = 30
MAX_FULL_MD = 200_000
MIN_CHRONOLOGY = 3


# --- input -------------------------------------------------------------------------------------

def load_events(cur, day_from, day_to):
    """Events of the window that reached the writer, one row per distinct title (latest run)."""
    end = (date.fromisoformat(day_to) + timedelta(days=1)).isoformat()
    cur.execute("""SELECT DISTINCT ON (e.title) e.id, e.title, e.event_type, t.slug,
                          i.id, i.title, i.url, i.published_at, i.raw_data
                     FROM knowledge.event e
                     JOIN knowledge.synthesis s ON s.event_id = e.id
                     JOIN knowledge.event_document d ON d.event_id = e.id
                     JOIN intelligence.items i ON i.id = d.item_id
                LEFT JOIN knowledge.event_topic et ON et.event_id = e.id AND et.is_main
                LEFT JOIN knowledge.topic t ON t.id = et.topic_id
                    WHERE i.published_at >= %s::date AND i.published_at < %s::date
                 ORDER BY e.title, e.id DESC""", (day_from, end))
    items = []
    for eid, etitle, etype, topic, iid, ititle, url, published, raw in cur.fetchall():
        body = ((raw or {}).get("content_capture") or {}).get("text") or ""
        items.append({"event_id": eid, "type": etype, "topic": topic, "item_id": iid, "title": ititle,
                      "url": url, "published": published.isoformat(), "outlet": (raw or {}).get("origin_title") or host(url or ""),
                      "text": (ititle + "\n" + body)[:MAX_EVENT_CHARS]})
    items.sort(key=lambda x: (x["published"], x["item_id"]))
    return items


def confirmed_companies(cur, event_ids):
    cur.execute("""SELECT DISTINCT p.mention_name, p.company_id FROM knowledge.event_participant p
                    WHERE p.event_id = ANY(%s) AND p.link_status = 'confirmed'""", (list(event_ids),))
    return [{"name": n, "company_id": c} for n, c in cur.fetchall()]


ARCHIVE_YEARS = 6


def archive_hits(cur, terms, before, limit=15):
    """Our own archive first: earlier items that mention the programme or distinctive terms."""
    pats = ["%" + t + "%" for t in terms if len(t) >= 4][:8]
    if not pats:
        return []
    start = (date.fromisoformat(before[:10]) - timedelta(days=365 * ARCHIVE_YEARS)).isoformat()
    cur.execute("""SELECT i.id, i.title, i.url, i.published_at, left(i.raw_data->'content_capture'->>'text', 1500)
                     FROM intelligence.items i
                    WHERE i.published_at < %s::date AND i.published_at >= %s::date
                      AND (i.title ILIKE ANY(%s) OR i.raw_data->'content_capture'->>'text' ILIKE ANY(%s))
                 ORDER BY i.published_at DESC LIMIT %s""", (before[:10], start, pats, pats, limit))
    return [{"archive_id": i, "title": t, "url": u, "date": p.date().isoformat(), "text": (t or "") + "\n" + (b or "")}
            for i, t, u, p, b in cur.fetchall()]


def doctrine_for(topic, records):
    return [r for r in records if topic and topic in (r.get("topics") or [])]


# --- grouping ----------------------------------------------------------------------------------

def valid_groups(answer, n):
    """Every item in exactly one group; otherwise each item is its own group."""
    groups = (answer or {}).get("groups") or []
    seen = [i for g in groups for i in g.get("items", [])]
    if sorted(seen) != list(range(1, n + 1)):
        return None
    return groups


def group_items(model, items):
    packet = {"items": [{"n": k + 1, "title": it["title"], "outlet": it["outlet"], "date": it["published"][:10],
                         "text": it["text"][:700]} for k, it in enumerate(items)]}
    answer, meta = model(GROUP_SYSTEM, packet, GROUP_SCHEMA, max_output=8000)
    groups = valid_groups(answer, len(items))
    if groups is None:
        groups = [{"items": [k + 1], "programme": it["title"], "organisations": [], "terms": [],
                   "place": None} for k, it in enumerate(items)]
        meta["fallback"] = "invalid_grouping"
    return groups, meta


# --- facts -------------------------------------------------------------------------------------

def human_amount(amount, currency):
    if amount in (None, ""):
        return ""
    x = float(amount)
    for scale, word in ((1e9, "billion"), (1e6, "million")):
        if x >= scale:
            return "%s %g %s" % (currency or "", round(x / scale, 3), word)
    return "%s %g" % (currency or "", x)


def anchor_for(page, row):
    """The section of an official page that holds the row's year (and amount if any)."""
    if not page or page.get("kind") != "html":
        return None
    for s in sections(page.get("markdown", "")):
        if check_row(row, s["heading"] + "\n" + s["text"]) == "ok":
            return s["anchor"]
    return None


def build_facts(event_items, research, pages, archive, doctrine):
    """Numbered facts for the writer; each keeps its source, verification status and anchor."""
    facts, dropped = [], {}

    def add(prefix, row, text, status, url=None, archive_id=None, anchor=None):
        fid = "%s%d" % (prefix, 1 + sum(f["id"].startswith(prefix) for f in facts))
        facts.append({"id": fid, "kind": prefix, "text": text, "status": status, "source_url": url,
                      "archive_id": archive_id, "anchor": anchor, "row": row})

    for it in event_items:
        add("N", None, it["text"], "news", url=it["url"])
    ps = research.get("primary_source") or {}
    page = pages.get(ps.get("url"))
    if page and page.get("kind") == "html":
        add("P", ps, "%s (%s, %s)\n%s" % (ps.get("title"), ps.get("publisher"), ps.get("date"),
                                         page["markdown"][:4000]), "ok", url=ps.get("url"))
    archive_by_id = {a["archive_id"]: a for a in archive}
    for row in research.get("timeline") or []:
        text = "%s (%s): %s Status: %s. %s" % (row.get("date"), row.get("precision"), row.get("what"),
                                               row.get("commitment"), human_amount(row.get("amount"), row.get("currency")))
        a = archive_by_id.get(row.get("archive_id"))
        if a:
            add("T", row, text.strip(), check_row(row, a["text"]), url=a["url"], archive_id=a["archive_id"])
            continue
        page = pages.get(row.get("source_url"))
        status = ("no_source" if not row.get("source_url") else "source_unreachable" if not page
                  else "pdf_not_read" if page["kind"] == "pdf" else check_row(row, page.get("markdown"))
                  if page["kind"] == "html" else "source_unreachable")
        add("T", row, text.strip(), status, url=row.get("source_url"), anchor=anchor_for(page, row))
    for row in research.get("supply_chain") or []:
        page = pages.get(row.get("source_url"))
        ok = bool(page and page.get("kind") == "html" and norm(row.get("lower")).split(" ")[0] in norm(page.get("markdown")))
        add("S", row, "%s depends on %s (%s): %s" % (row.get("upper"), row.get("lower"), row.get("level"), row.get("note")),
            "ok" if ok else "unconfirmed", url=row.get("source_url"))
    for row in research.get("restrictions") or []:
        page = pages.get(row.get("source_url"))
        probe = {"date": row.get("starts_on") or row.get("ends_on") or ""}
        status = check_row(probe, page.get("markdown")) if page and page.get("kind") == "html" else "source_unreachable"
        add("R", row, "%s %s on %s (target: all). From %s to %s, status %s." % (
            row.get("issuer"), row.get("kind"), row.get("subject"), row.get("starts_on"), row.get("ends_on"),
            row.get("status")), status, url=row.get("source_url"))
    for d in doctrine:
        add("D", d, "%s, %s (%s): %s" % (d["jurisdiction"], d["title"], d["document_date"], d["direction"]),
            "ok" if d.get("verified") else "check", url=d.get("source_url"))
    for f in facts:
        if f["status"] not in ("ok", "news"):
            dropped[f["status"]] = dropped.get(f["status"], 0) + 1
    return facts, dropped


def writer_packet(event_items, facts):
    usable = [f for f in facts if f["status"] in ("ok", "news", "check")]
    return {"event": {"titles": [it["title"] for it in event_items], "date": event_items[0]["published"][:10]},
            "facts": [{"id": f["id"], "text": f["text"]} for f in usable]}


# --- checks and admission ----------------------------------------------------------------------

NUMBER = re.compile(r"\d[\d,.]*\d|\d")


def numbers_in(text):
    out = []
    for m in NUMBER.findall(text or ""):
        n = m.replace(",", "").rstrip(".")
        if len(n.replace(".", "")) >= 2:
            out.append(n)
    return out


def check_text(output, facts):
    by_id = {f["id"]: f for f in facts}
    codes, sentences = set(), 0
    if len(output.get("headline") or "") > 90:
        codes.add("headline_too_long")
    for sec in SECTIONS:
        for s in output.get(sec) or []:
            sentences += 1
            refs = [r for r in s.get("refs") or [] if r in by_id]
            if not refs:
                codes.add("sentence_without_source"); continue
            pool = " ".join(by_id[r]["text"] for r in refs)
            pool_n = pool.replace(",", "")
            extra = [x for r in refs for x in amount_forms((by_id[r]["row"] or {}).get("amount")
                                                          if isinstance(by_id[r]["row"], dict) else None)]
            for n in numbers_in(s.get("text")):
                if n not in pool_n and n not in extra:
                    codes.add("number_not_in_sources")
    for n in numbers_in(output.get("headline", "") + " " + output.get("lead", "")):
        if not any(n in f["text"].replace(",", "") for f in facts):
            codes.add("number_not_in_sources")
    return sorted(codes), sentences


def admit(facts, output, codes):
    """§6: chronology (≥3 verified dated rows) AND (supply chain/restriction OR doctrine used)."""
    used = {r for sec in SECTIONS for s in output.get(sec) or [] for r in s.get("refs") or []}
    dates = {f["row"].get("date") for f in facts if f["kind"] == "T" and f["status"] == "ok"}
    horizontal = len(dates) >= MIN_CHRONOLOGY
    vertical = any(f["kind"] in ("S", "R") and f["status"] == "ok" and f["id"] in used for f in facts)
    doctrine = any(f["kind"] == "D" and f["id"] in used for f in facts)
    reasons = list(codes)
    if not horizontal:
        reasons.append("analysis_missing_chronology")
    if not (vertical or doctrine):
        reasons.append("analysis_missing_vertical_and_doctrine")
    return ("CANDIDATE" if not reasons else "HOLD"), reasons, {
        "chronology_dates": len(dates), "vertical": vertical, "doctrine": doctrine}


# --- documents ---------------------------------------------------------------------------------

def documents(research, facts, pages, terms, out_dir):
    """Cards for every cited source; official HTML → Markdown full text and anchored fragments."""
    urls = {}
    for d in research.get("documents") or []:
        urls[d.get("url")] = {"title": d.get("title"), "doc_type": d.get("doc_type"), "issuer": d.get("issuer"),
                              "date": d.get("date")}
    ps = research.get("primary_source") or {}
    urls.setdefault(ps.get("url"), {"title": ps.get("title"), "doc_type": "primary_source",
                                    "issuer": ps.get("publisher"), "date": ps.get("date")})
    for f in facts:
        if f["source_url"] and f["kind"] in ("T", "S", "R", "D"):
            urls.setdefault(f["source_url"], {"title": None, "doc_type": "source", "issuer": host(f["source_url"]),
                                              "date": None})
    cards = []
    low_terms = [t.lower() for t in terms if len(t) >= 4]
    for url, meta in urls.items():
        if not url:
            continue
        page = pages.get(url) or {}
        doc_id = sha(url)[:12]
        official = is_official(url)
        card = {"id": doc_id, "url": url, "host": host(url), "official": official, "format": page.get("kind", "unread"),
                "title": meta.get("title") or page.get("title"), "doc_type": meta.get("doc_type"),
                "issuer": meta.get("issuer"), "date": meta.get("date"), "storage": "link_only", "anchors": []}
        if official and page.get("kind") == "html" and page.get("markdown"):
            secs = sections(page["markdown"])
            keep = [s for s in secs if any(t in (s["heading"] + " " + s["text"]).lower() for t in low_terms)]
            cited = {f["anchor"] for f in facts if f["source_url"] == url and f["anchor"]}
            keep += [s for s in secs if s["anchor"] in cited and s not in keep]
            d = out_dir / "docs" / doc_id
            (d / "f").mkdir(parents=True, exist_ok=True)
            (d / "full.md").write_text(page["markdown"][:MAX_FULL_MD], encoding="utf-8")
            for s in keep:
                (d / "f" / (s["anchor"] + ".md")).write_text("## %s\n\n%s" % (s["heading"], s["text"]), encoding="utf-8")
            card.update(storage="markdown", text_sha256=sha(page["markdown"]), size=len(page["markdown"].encode()),
                        anchors=[{"anchor": s["anchor"], "heading": s["heading"]} for s in keep])
        cards.append(card)
    for f in facts:
        f["document_id"] = sha(f["source_url"])[:12] if f["source_url"] else None
    return cards


# --- run ---------------------------------------------------------------------------------------

def research_event(model, cur, group, event_items, doctrine_records, schema_mode):
    before = event_items[0]["published"]
    terms = [group.get("programme") or ""] + (group.get("organisations") or []) + (group.get("terms") or [])
    terms = [t.strip() for t in terms if t and t.strip()]
    archive = archive_hits(cur, terms, before) if cur is not None else []
    topic = event_items[0]["topic"]
    doctrine = doctrine_for(topic, doctrine_records)
    packet = {"news": [{"title": it["title"], "outlet": it["outlet"], "date": it["published"][:10], "text": it["text"]}
                       for it in event_items],
              "programme_hint": group.get("programme"), "topic": topic,
              "our_archive": [{"archive_id": a["archive_id"], "date": a["date"], "title": a["title"],
                               "url": a["url"], "text": a["text"][:600]} for a in archive],
              "doctrine_known": [{"title": d["title"], "jurisdiction": d["jurisdiction"], "date": d["document_date"]}
                                 for d in doctrine]}
    research, meta = model(RESEARCH_SYSTEM, packet, RESEARCH_SCHEMA, grounded=True, max_output=8000,
                           schema_mode=schema_mode)
    return research, meta, archive, doctrine, terms


def cited_urls(research):
    urls = [(research.get("primary_source") or {}).get("url")]
    for key in ("timeline", "supply_chain", "restrictions"):
        urls += [r.get("source_url") for r in research.get(key) or []]
    urls += [d.get("url") for d in research.get("documents") or []]
    return [u for u in urls if u][:MAX_URLS_PER_EVENT]


def run(cur, model, items, companies_by_event, doctrine_records, out_dir, schema_mode="schema", fetcher=fetch_all):
    if not items:
        return {"version": PILOT_VERSION, "items": 0, "groups": 0, "events": [], "stop": "NO_EVENTS_IN_WINDOW"}
    groups, gmeta = group_items(model, items)
    result = {"version": PILOT_VERSION, "model": MODEL, "price_version": PRICE_VERSION, "items": len(items),
              "groups": len(groups), "grouping": {"cost_micro_usd": gmeta["cost_micro_usd"],
                                                  "fallback": gmeta.get("fallback")}, "events": []}
    for g in groups:
        ev_items = [items[n - 1] for n in g["items"]]
        record = {"titles": [it["title"] for it in ev_items], "item_ids": [it["item_id"] for it in ev_items],
                  "old_event_ids": [it["event_id"] for it in ev_items], "topic": ev_items[0]["topic"],
                  "type": ev_items[0]["type"], "date": ev_items[0]["published"][:10], "place": g.get("place"),
                  "programme": g.get("programme"), "news": [{"title": it["title"], "url": it["url"], "outlet": it["outlet"],
                                                             "date": it["published"][:10]} for it in ev_items]}
        result["events"].append(record)
        try:
            research, rmeta, archive, doctrine, terms = research_event(model, cur, g, ev_items, doctrine_records,
                                                                       schema_mode)
            pages = fetcher(cited_urls(research))
            facts, dropped = build_facts(ev_items, research, pages, archive, doctrine)
            cards = documents(research, facts, pages, terms, out_dir)
            output, wmeta = model(WRITER_SYSTEM, writer_packet(ev_items, facts), WRITER_SCHEMA, max_output=6000)
            codes, sentences = check_text(output, facts)
            decision, reasons, analysis = admit(facts, output, codes)
        except BudgetStop as stop:  # the run stops here; finished events are kept for review
            record["outcome"] = "stopped"
            result["stop"] = str(stop)
            break
        except Exception as exc:  # one event fails alone; the run goes on
            record.update(outcome="error", error=type(exc).__name__ + ":" + str(exc)[:120])
            continue
        record.update(outcome=decision, reasons=reasons, analysis=analysis, parent=research.get("parent"),
                      primary_source=research.get("primary_source"), archive_hits=len(archive),
                      search_queries=rmeta["search_queries"], grounding_chunks=len(rmeta["grounding_chunks"]),
                      cost_micro_usd=rmeta["cost_micro_usd"] + wmeta["cost_micro_usd"], sentences=sentences,
                      facts=[{k: f[k] for k in ("id", "kind", "text", "status", "source_url", "archive_id", "anchor",
                                                "document_id")} for f in facts],
                      dropped=dropped, documents=cards, synthesis=output,
                      companies_confirmed=[c for e in record["old_event_ids"] for c in companies_by_event.get(e, [])])
    return result


def summary(result, ledger):
    """Public log line: counts, codes and cost only."""
    outcomes, reasons = {}, {}
    for e in result["events"]:
        outcomes[e.get("outcome")] = outcomes.get(e.get("outcome"), 0) + 1
        for r in e.get("reasons") or []:
            reasons[r] = reasons.get(r, 0) + 1
    return {"version": result["version"], "items": result["items"], "groups": result["groups"],
            "outcomes": outcomes, "reasons": reasons, "spent_micro_usd": ledger.spent, "model_calls": ledger.calls,
            "search_queries": ledger.search_queries,
            "documents_markdown": sum(1 for e in result["events"] for d in e.get("documents") or [] if d["storage"] == "markdown"),
            "documents_total": sum(len(e.get("documents") or []) for e in result["events"]),
            "chronology_rows_ok": sum(1 for e in result["events"] for f in e.get("facts") or []
                                      if f["kind"] == "T" and f["status"] == "ok"),
            "stop": result.get("stop")}


def main(argv=None):
    from ..synthesis.live import connect
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day-from", required=True)
    parser.add_argument("--day-to", required=True)
    parser.add_argument("--limit", type=int, default=0, help="first N items only (0 = all)")
    parser.add_argument("--schema-mode", choices=["schema", "text"], default="schema")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    doctrine_records = json.loads(DOCTRINE.read_text(encoding="utf-8"))["records"]
    ledger = Ledger()
    model = Gemini(os.environ.get("GEMINI_API_KEY"), ledger)
    with connect() as conn:
        with conn.cursor() as cur:
            items = load_events(cur, args.day_from, args.day_to)
            if args.limit:
                items = items[:args.limit]
            by_event = {it["event_id"]: confirmed_companies(cur, [it["event_id"]]) for it in items}
            try:
                result = run(cur, model, items, by_event, doctrine_records, out_dir, args.schema_mode)
            except BudgetStop as stop:
                result = {"version": PILOT_VERSION, "items": len(items), "groups": 0, "events": [], "stop": str(stop)}
            conn.rollback()  # read-only: nothing to keep
    result["spent_micro_usd"], result["search_queries"] = ledger.spent, ledger.search_queries
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("KB_PILOT_RESULT " + canonical(summary(result, ledger)))
    return 1 if result.get("stop") else 0
