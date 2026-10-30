"""EXP-028 pilot run: the events of a day window → dossier rows, documents and syntheses for review.

Steps per DESIGN §8 v1.1: (1) group duplicates and get search keys (Gemini); (2) our archive first
(intelligence.items); (3) research with Google Search (Gemini, grounded); (4) code reads every cited page and
checks dates and amounts, official HTML becomes Markdown with anchors; (5) writer on verified facts only;
(6) code checks numbers and references, then the §6 rule: no chronology and no supply chain/doctrine → HOLD.
v1.2 (DESIGN §12): verified rows are kept per programme (dossier, migration v16) and reused, so research asks
only for what is new; each model step can go as one Batch request (−50%); spend is kept per step.
Only the dossier tables are written. The public log gets counts and cost only.
"""
import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path
import re
import sys

from . import PILOT_VERSION
from .dossier import DbStore, KIND, MemoryStore, PREFIX, row_hash
from .gemini import Gemini, Ledger
from .pages import amount_forms, check_row, fetch_all, host, is_official, norm, sections
from .free import FreeModel
from .prompts import (GROUP_SCHEMA, GROUP_SYSTEM, RESEARCH_SCHEMA, RESEARCH_SYSTEM, SECTIONS, SHORT_WRITER_SCHEMA,
                      SHORT_WRITER_SYSTEM, STRUCTURE_SYSTEM, WRITER_SCHEMA, WRITER_SYSTEM)
from ..synthesis.contracts import canonical, sha
from ..synthesis.live import BudgetStop, MODEL, PRICE_VERSION

ROOT = Path(__file__).resolve().parent
DOCTRINE = ROOT / "resources" / "doctrine_v2.json"
MAX_EVENT_CHARS = 5000
MAX_URLS_PER_EVENT = 30
MAX_FULL_MD = 200_000
MIN_CHRONOLOGY = 3
RESEARCH_THINKING = os.environ.get("KB_RESEARCH_THINKING", "low")
TOPICS = ROOT.parent / "synthesis" / "resources" / "topics_v3.json"


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


def load_items(cur, day_from, day_to, min_body=300):
    """All news items of the window with a captured body; no relevance or importance filter (D-…-COST)."""
    end = (date.fromisoformat(day_to) + timedelta(days=1)).isoformat()
    cur.execute("""SELECT i.id, i.title, i.url, i.published_at, i.raw_data FROM intelligence.items i
                    WHERE i.published_at >= %s::date AND i.published_at < %s::date
                      AND length(i.raw_data->'content_capture'->>'text') >= %s
                 ORDER BY i.published_at, i.id""", (day_from, end, min_body))
    out = []
    for iid, title, url, published, raw in cur.fetchall():
        body = raw["content_capture"]["text"]
        out.append({"event_id": None, "type": None, "topic": None, "item_id": iid, "title": title, "url": url,
                    "published": published.isoformat(), "outlet": (raw or {}).get("origin_title") or host(url or ""),
                    "text": (title + "\n" + body)[:MAX_EVENT_CHARS]})
    return out


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


def archive_search(cur, terms, before, item_ids):
    """Meaning first (embeddings, one item per origin), then keywords; v2 step 1."""
    if cur is None:
        return []
    hits = []
    try:
        from .embed import similar_items
        hits = similar_items(cur, item_ids, before)
    except Exception:  # no embeddings yet for these items, or the extension is absent: keywords only
        if hasattr(cur, "connection"):
            cur.connection.rollback()
    seen = {h["archive_id"] for h in hits}
    hits += [h for h in archive_hits(cur, terms, before) if h["archive_id"] not in seen]
    return hits[:20]


EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU",
      "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
NATO = (EU - {"AT", "CY", "IE", "MT"}) | {"US", "CA", "GB", "NO", "IS", "TR", "AL", "ME", "MK"}


def doctrine_for(topic, records, countries):
    """Doctrine of the event's topic AND of a country involved (EU/NATO when a member is involved)."""
    c = {x.upper() for x in countries or []}
    c = c | ({"EU"} if c & EU else set()) | ({"NATO"} if c & NATO else set())
    return [r for r in records if topic and topic in (r.get("topics") or []) and r.get("jurisdiction") in c]


# --- grouping ----------------------------------------------------------------------------------

def valid_groups(answer, n):
    """Every item in exactly one group; otherwise each item is its own group."""
    groups = (answer or {}).get("groups") or []
    seen = [i for g in groups for i in g.get("items", [])]
    if sorted(seen) != list(range(1, n + 1)):
        return None
    return groups


GROUP_CHUNK = 40  # 158 items in one grouping call cut the answer (run 41090801240: MODEL_INCOMPLETE_OUTPUT)


def group_chunk(model, items, known_programmes, topics):
    packet = {"items": [{"n": k + 1, "title": it["title"], "outlet": it["outlet"], "date": it["published"][:10],
                         "text": it["text"][:700]} for k, it in enumerate(items)],
              "known_programmes": list(known_programmes), "topics": list(topics)}
    try:
        answer, meta = model(GROUP_SYSTEM, packet, GROUP_SCHEMA, max_output=16000, step="grouping")
    except BudgetStop:
        raise
    except Exception as exc:  # one failed chunk falls back to one group per item, the run goes on
        answer, meta = None, {"cost_micro_usd": 0, "error": type(exc).__name__ + ":" + str(exc)[:80]}
    groups = valid_groups(answer, len(items))
    if groups is None:
        groups = [{"items": [k + 1], "programme": None, "organisations": [], "terms": [it["title"]],
                   "place": None} for k, it in enumerate(items)]  # no programme: a headline is not a programme name
        meta["fallback"] = "invalid_grouping"
    return groups, meta


def group_items(model, items, known_programmes=(), topics=()):
    """Group in chunks of GROUP_CHUNK items (in time order); item numbers stay global."""
    groups, meta = [], {"cost_micro_usd": 0}
    for start in range(0, len(items), GROUP_CHUNK):
        part, m = group_chunk(model, items[start:start + GROUP_CHUNK], known_programmes, topics)
        for g in part:
            g["items"] = [n + start for n in g["items"]]
        groups += part
        meta["cost_micro_usd"] += m.get("cost_micro_usd") or 0
        for key in ("fallback", "error"):
            if m.get(key):
                meta.setdefault(key, m[key])
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


def rescue(row, status, url, grounded_pages):
    """A row whose cited page fails is checked against the pages search actually returned."""
    if status == "ok":
        return status, url, None
    for gurl, page in grounded_pages.items():
        if page and page.get("kind") == "html" and check_row(row, page.get("markdown")) == "ok":
            return "ok", page.get("final_url") or gurl, page
    return status, url, None


def real_url(url, pages):
    """Search sources are Google redirect links; keep the address the redirect led to."""
    page = pages.get(url) if url else None
    return (page or {}).get("final_url") or url


def row_text(prefix, row):
    if prefix == "T":
        return ("%s (%s): %s Status: %s. %s" % (row.get("date"), row.get("precision"), row.get("what"),
                                                row.get("commitment"), human_amount(row.get("amount"), row.get("currency")))).strip()
    if prefix == "S":
        return "%s depends on %s (%s): %s" % (row.get("upper"), row.get("lower"), row.get("level"), row.get("note"))
    return "%s %s on %s. From %s to %s, status %s." % (row.get("issuer"), row.get("kind"), row.get("subject"),
                                                      row.get("starts_on"), row.get("ends_on"), row.get("status"))


def build_facts(event_items, research, pages, archive, doctrine, grounded_pages=None, dossier=()):
    """Numbered facts for the writer; each keeps its source, verification status and anchor.

    Dossier rows were verified on an earlier run and come first; a new row that repeats one is dropped.
    """
    grounded_pages = grounded_pages or {}
    facts, dropped = [], {}

    def add(prefix, row, text, status, url=None, archive_id=None, anchor=None, origin="new"):
        fid = "%s%d" % (prefix, 1 + sum(f["id"].startswith(prefix) for f in facts))
        facts.append({"id": fid, "kind": prefix, "text": text, "status": status, "source_url": url,
                      "archive_id": archive_id, "anchor": anchor, "row": row, "origin": origin})

    for it in event_items:
        add("N", None, it["text"], "news", url=it["url"])
    known = set()
    for e in dossier:
        prefix = PREFIX[e["kind"]]
        known.add(row_hash(e["kind"], e["row"], e["source_url"], e["archive_id"]))
        add(prefix, e["row"], row_text(prefix, e["row"]), "ok", url=e["source_url"], archive_id=e["archive_id"],
            anchor=e.get("anchor"), origin="dossier")
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
        status, url, gpage = rescue(row, status, row.get("source_url"), grounded_pages)
        add("T", row, text.strip(), status, url=url, anchor=anchor_for(gpage or page, row))
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
        if f["source_url"] and f["kind"] in ("P", "T", "S", "R") and f["origin"] == "new":
            f["source_url"] = real_url(f["source_url"], pages)
    repeats = [f for f in facts if f["origin"] == "new" and f["kind"] in KIND and isinstance(f["row"], dict)
               and row_hash(KIND[f["kind"]], f["row"], f["source_url"], f["archive_id"]) in known]
    for f in repeats:
        f["status"] = "dossier_repeat"
    for f in facts:
        if f["status"] not in ("ok", "news", "dossier_repeat"):
            dropped[f["status"]] = dropped.get(f["status"], 0) + 1
    return facts, dropped


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MAX_CHRONOLOGY_LINES = 7


def human_date(day, precision):
    if not day or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return day or ""
    y, m, d = day.split("-")
    if precision == "year":
        return y
    if precision == "month":
        return "%s %s" % (MONTHS[int(m) - 1], y)
    return "%d %s %s" % (int(d), MONTHS[int(m) - 1], y)


def compose_sections(facts):
    """Context, supply chain and policy sentences written by code from verified rows (no model, no invention)."""
    ok = [f for f in facts if f["status"] == "ok" and isinstance(f.get("row"), dict)]
    chron = sorted([f for f in ok if f["kind"] == "T"], key=lambda f: f["row"].get("date") or "")
    seen, unique = set(), []
    for f in chron:  # one line per date
        if f["row"].get("date") not in seen:
            seen.add(f["row"].get("date")); unique.append(f)
    if len(unique) > MAX_CHRONOLOGY_LINES:  # the earliest step for depth, then the latest ones
        unique = unique[:1] + unique[-(MAX_CHRONOLOGY_LINES - 1):]
    context = []
    for f in unique:
        r = f["row"]
        money = ", ".join(x for x in (r.get("commitment") if r.get("commitment") not in (None, "other") else None,
                                      human_amount(r.get("amount"), r.get("currency")).strip() or None) if x)
        what = (r.get("what") or "").strip().rstrip(".")
        context.append({"text": "%s: %s%s." % (human_date(r.get("date"), r.get("precision")), what,
                                               " (%s)" % money if money else ""), "refs": [f["id"]]})
    supply = []
    for f in [f for f in ok if f["kind"] == "S"][:3]:
        r = f["row"]
        note = (r.get("note") or "").strip().rstrip(".")
        if norm(r.get("lower") or "") and norm(r.get("lower"))[:40] in norm(note):
            note = ""  # the note only repeats the link
        supply.append({"text": "%s depends on %s%s." % (r.get("upper"), r.get("lower"), " — " + note if note else ""),
                       "refs": [f["id"]]})
    for f in [f for f in ok if f["kind"] == "R"][:2]:
        r = f["row"]
        span = " ".join(x for x in ("from " + r["starts_on"] if r.get("starts_on") else "",
                                    "until " + r["ends_on"] if r.get("ends_on") else "") if x)
        supply.append({"text": "%s: %s on %s%s (%s)." % (r.get("issuer"), (r.get("kind") or "").replace("_", " "),
                                                        r.get("subject"), " " + span if span else "", r.get("status")),
                       "refs": [f["id"]]})
    policy = [{"text": "%s — %s (%s): %s" % (f["row"]["jurisdiction"], f["row"]["title"], f["row"]["document_date"],
                                             f["row"]["direction"]), "refs": [f["id"]]}
              for f in facts if f["kind"] == "D" and f["status"] in ("ok", "check")][:2]
    return {"context": context, "supply_chain": supply, "policy_direction": policy}


MAX_WRITER_FACTS = 30


def short_writer_packet(event_items, facts, composed):
    """News, primary source, the facts the composed sections cite and the newest chronology — not the whole
    dossier (run 40939444092: up to 139 facts per event overloaded the writer)."""
    cited = {r for sec in composed.values() for s in sec for r in s["refs"]}
    keep = [f for f in facts if f["kind"] in ("N", "P") or f["id"] in cited]
    rest = sorted([f for f in facts if f not in keep and f["status"] in ("ok", "check") and f["kind"] == "T"],
                  key=lambda f: (f.get("row") or {}).get("date") or "", reverse=True)
    keep += rest[:max(0, MAX_WRITER_FACTS - len(keep))]
    base = writer_packet(event_items, keep)
    base["composed"] = composed
    return base


def merge_writer(short, composed):
    out = dict(short)
    out.update(composed)
    return out


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


REF_MARK = re.compile(r"\s*\[(?:[A-Z]\d+(?:,\s*)?)+\]")


REF_ID = re.compile(r"\b[A-Z]\d+\b")


def normalize_output(output):
    """Writer sections as lists of {text, refs}; a bare string keeps the ids it cites in brackets."""
    if not isinstance(output, dict):
        raise ValueError("WRITER_NOT_OBJECT")
    for sec in SECTIONS:
        items = output.get(sec)
        if isinstance(items, (str, dict)):
            items = [items]
        fixed = []
        for s in items or []:
            if isinstance(s, str):
                refs = [r for m in re.findall(r"\[([^\]]+)\]", s) for r in REF_ID.findall(m)]
                fixed.append({"text": s, "refs": refs})
            elif isinstance(s, dict):
                fixed.append({"text": str(s.get("text") or ""), "refs": [str(r) for r in s.get("refs") or []]})
        output[sec] = fixed
    for key in ("headline", "lead"):
        output[key] = str(output.get(key) or "")
    if not isinstance(output.get("companies"), list):
        output["companies"] = []
    return output


def strip_refs(output):
    """References live in refs; bracketed ids the model repeats inside the text are removed."""
    for sec in SECTIONS:
        for s in output.get(sec) or []:
            s["text"] = REF_MARK.sub("", s.get("text") or "").strip()


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
    if not (output.get("headline") or "").strip() or not output.get("what_happened"):
        reasons.append("writer_incomplete")
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
    final = {(p or {}).get("final_url"): p for p in pages.values() if p and p.get("final_url")}
    pages = {**pages, **final}
    for d in research.get("documents") or []:
        urls[real_url(d.get("url"), pages)] = {"title": d.get("title"), "doc_type": d.get("doc_type"), "issuer": d.get("issuer"),
                              "date": d.get("date")}
    ps = research.get("primary_source") or {}
    urls.setdefault(real_url(ps.get("url"), pages), {"title": ps.get("title"), "doc_type": "primary_source",
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
                "fetch_error": page.get("error"),
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

def prepare(cur, group, event_items, doctrine_records, store):
    """Everything the research step needs: archive hits, doctrine, the programme's dossier."""
    before = event_items[0]["published"]
    terms = [group.get("programme") or ""] + (group.get("organisations") or []) + (group.get("terms") or [])
    terms = [t.strip() for t in terms if t and t.strip()]
    topic = event_items[0]["topic"] or group.get("topic")
    programme = store.find(group.get("programme")) if store else None
    dossier = store.load(programme["id"]) if programme else []
    return {"items": event_items, "group": group, "terms": terms, "topic": topic, "programme": programme,
            "dossier": dossier, "archive": archive_search(cur, terms, before, [it["item_id"] for it in event_items]),
            "doctrine": doctrine_for(topic, doctrine_records, group.get("countries"))}


def research_request(model, ev):
    items, group, dossier = ev["items"], ev["group"], ev["dossier"]
    chron = [e for e in dossier if e["kind"] == "chronology"]
    last = max((e["row"].get("date") or "" for e in chron), default="")
    has_chain = any(e["kind"] != "chronology" for e in dossier)
    packet = {"news": [{"title": it["title"], "outlet": it["outlet"], "date": it["published"][:10], "text": it["text"]}
                       for it in items],
              "programme_hint": group.get("programme"), "topic": ev["topic"],
              "our_archive": [{"archive_id": a["archive_id"], "date": a["date"], "title": a["title"],
                               "url": a["url"], "text": a["text"][:600]} for a in ev["archive"]],
              "doctrine_known": [{"title": d["title"], "jurisdiction": d["jurisdiction"], "date": d["document_date"]}
                                 for d in ev["doctrine"]],
              "dossier_known": [{"date": e["row"].get("date"), "what": e["row"].get("what") or e["row"].get("lower")}
                                for e in dossier]}
    head = "Search the web with Google Search. Event: %s (%s). " % (items[0]["title"], items[0]["published"][:10])
    if len(chron) >= MIN_CHRONOLOGY:
        task = head + ("Our dossier already holds %d verified steps of %s up to %s (dossier_known): do not repeat them. "
                       "Research the announcement itself and only steps after %s%s, then write the research note."
                       % (len(chron), group.get("programme") or "the programme", last, last,
                          "" if has_chain else ", and the supply chain of the product"))
    else:
        task = head + ("Research the announcement itself, the history of %s, and the supply chain of the product, "
                       "then write the research note." % (group.get("programme") or "the programme"))
    return model.request(RESEARCH_SYSTEM, packet, RESEARCH_SCHEMA, grounded=True, max_output=10000,
                         schema_mode="plain", thinking=RESEARCH_THINKING, task=task, step="research")


def structure_request(model, ev, note, info):
    sources = [{"n": k + 1, "uri": c.get("uri"), "title": c.get("title")} for k, c in enumerate(info["grounding_chunks"])]
    return model.request(STRUCTURE_SYSTEM, {"note": mark_sources(note, info["supports"]), "sources": sources,
                                            "archive_ids": [a["archive_id"] for a in ev["archive"]]},
                         RESEARCH_SCHEMA, max_output=8000, step="structure")


def mark_sources(note, supports):
    """Append [n] (1-based source numbers) after each grounded segment of the note."""
    for sup in supports:
        seg = sup["text"]
        if seg and sup["chunks"] and seg in note:
            mark = "".join("[%d]" % (i + 1) for i in sup["chunks"])
            note = note.replace(seg, seg + " " + mark, 1)
    return note


def cited_urls(research):
    urls = [(research.get("primary_source") or {}).get("url")]
    for key in ("timeline", "supply_chain", "restrictions"):
        urls += [r.get("source_url") for r in research.get(key) or []]
    urls += [d.get("url") for d in research.get("documents") or []]
    return [u for u in urls if u][:MAX_URLS_PER_EVENT]


def topic_slugs():
    try:
        return [t["slug"] for t in json.loads(TOPICS.read_text(encoding="utf-8"))["topics"]]
    except OSError:
        return []


def run(cur, model, items, companies_by_event, doctrine_records, out_dir, fetcher=fetch_all, store=None,
        run_ref=None, save=None, cheap=None, template=True):
    """Phases over all events: grouping → research → structure → pages and facts → writer → checks → dossier.

    Each model phase is one call per event, or one Batch request for all events when the model is in batch mode.
    save(ev, record, facts, cards) commits the event's verified rows to the dossier.
    """
    if not items:
        return {"version": PILOT_VERSION, "items": 0, "groups": 0, "events": [], "stop": "NO_EVENTS_IN_WINDOW"}
    store = store if store is not None else MemoryStore()
    cheap = cheap or model  # steps without search: free model with Gemini fallback when configured
    groups, gmeta = group_items(cheap, items, store.known_names(), topic_slugs())
    result = {"version": PILOT_VERSION, "model": MODEL, "price_version": PRICE_VERSION, "items": len(items),
              "groups": len(groups), "grouping": {"cost_micro_usd": gmeta["cost_micro_usd"],
                                                  "fallback": gmeta.get("fallback")}, "events": []}
    evs = []
    for g in groups:
        ev_items = [items[n - 1] for n in g["items"]]
        ev = prepare(cur, g, ev_items, doctrine_records, store)
        ev["record"] = {"titles": [it["title"] for it in ev_items], "item_ids": [it["item_id"] for it in ev_items],
                        "old_event_ids": [it["event_id"] for it in ev_items if it["event_id"]], "topic": ev["topic"],
                        "type": ev_items[0]["type"], "date": ev_items[0]["published"][:10], "place": g.get("place"),
                        "programme": g.get("programme"), "countries": g.get("countries"),
                        "dossier_rows_before": len(ev["dossier"]), "known_programme": bool(ev["programme"]),
                        "news": [{"title": it["title"], "url": it["url"], "outlet": it["outlet"],
                                  "date": it["published"][:10]} for it in ev_items]}
        result["events"].append(ev["record"])
        evs.append(ev)

    def fail(ev, exc):
        ev["record"].update(outcome="error", error=type(exc).__name__ + ":" + str(exc)[:120])

    def phase(live, make, apply, m=model):
        answers = m.many([make(ev) for ev in live])
        nxt = []
        for ev, a in zip(live, answers):
            if a[0] == "error":
                fail(ev, a[1]); continue
            try:
                apply(ev, a[1], a[2]); nxt.append(ev)
            except Exception as exc:
                fail(ev, exc)
        return nxt

    try:
        live = phase(evs, lambda ev: research_request(model, ev),
                     lambda ev, note, info: ev.update(note=note, rinfo=info))
        live = phase(live, lambda ev: structure_request(cheap, ev, ev["note"], ev["rinfo"]),
                     lambda ev, research, info: ev.update(research=research, sinfo=info), cheap)
        for ev in list(live):
            try:
                research, rinfo = ev["research"], ev["rinfo"]
                grounded = [c["uri"] for c in rinfo["grounding_chunks"] if c.get("uri")][:15]
                pages = fetcher(cited_urls(research) + grounded)
                facts, dropped = build_facts(ev["items"], research, pages, ev["archive"], ev["doctrine"],
                                             {u: pages.get(u) for u in grounded}, ev["dossier"])
                ev.update(facts=facts, dropped=dropped, cards=documents(research, facts, pages, ev["terms"], out_dir))
            except Exception as exc:
                fail(ev, exc); live.remove(ev)
        if template:  # code writes context / supply chain / policy; the model writes the short parts
            for ev in live:
                ev["composed"] = compose_sections(ev["facts"])
            live = phase(live, lambda ev: cheap.request(SHORT_WRITER_SYSTEM,
                                                        short_writer_packet(ev["items"], ev["facts"], ev["composed"]),
                                                        SHORT_WRITER_SCHEMA, max_output=2000, step="writer"),
                         lambda ev, output, info: ev.update(output=merge_writer(output, ev["composed"]), winfo=info),
                         cheap)
        else:
            live = phase(live, lambda ev: model.request(WRITER_SYSTEM, writer_packet(ev["items"], ev["facts"]),
                                                        WRITER_SCHEMA, max_output=6000, step="writer"),
                         lambda ev, output, info: ev.update(output=output, winfo=info))
    except BudgetStop as stop:  # finished events are kept; unfinished ones are marked
        result["stop"] = str(stop)
        live = [ev for ev in evs if "output" in ev]
        for ev in evs:
            if "output" not in ev and not ev["record"].get("outcome"):
                ev["record"]["outcome"] = "stopped"
    for ev in live:
        facts, rinfo, rec = ev["facts"], ev["rinfo"], ev["record"]
        try:
            output = normalize_output(ev["output"])
            strip_refs(output)
            codes, sentences = check_text(output, facts)
            decision, reasons, analysis = admit(facts, output, codes)
        except Exception as exc:  # one malformed answer fails its event only
            fail(ev, exc); continue
        rec.update(outcome=decision, reasons=reasons, analysis=analysis, parent=ev["research"].get("parent"),
                   primary_source=ev["research"].get("primary_source"), archive_hits=len(ev["archive"]),
                   research_keys={k: (len(v) if isinstance(v, list) else type(v).__name__)
                                  for k, v in ev["research"].items()},
                   search_queries=rinfo["search_queries"], grounding_chunks=rinfo["grounding_chunks"][:30],
                   cost_micro_usd=rinfo["cost_micro_usd"] + ev["sinfo"]["cost_micro_usd"] + ev["winfo"]["cost_micro_usd"],
                   sentences=sentences, dropped=ev["dropped"], documents=ev["cards"], synthesis=output,
                   facts=[{k: f.get(k) for k in ("id", "kind", "text", "status", "source_url", "archive_id", "anchor",
                                                 "document_id", "origin")} for f in facts],
                   companies_confirmed=[c for e in rec["old_event_ids"] for c in companies_by_event.get(e, [])])
        if save:
            try:
                rec["dossier"] = save(ev, rec, facts, ev["cards"])
            except Exception as exc:  # the synthesis stays; only the dossier write of this event failed
                rec["dossier"] = {"error": type(exc).__name__ + ":" + str(exc)[:160]}
    result["batches"] = getattr(model, "batches", [])
    result["free"] = getattr(cheap, "stats", None)
    return result


def summary(result, ledger):
    """Public log line: counts, codes and cost only."""
    outcomes, reasons = {}, {}
    for e in result["events"]:
        outcomes[e.get("outcome")] = outcomes.get(e.get("outcome"), 0) + 1
        for r in e.get("reasons") or []:
            reasons[r] = reasons.get(r, 0) + 1
    facts = [f for e in result["events"] for f in e.get("facts") or []]
    return {"version": result["version"], "items": result["items"], "groups": result["groups"],
            "outcomes": outcomes, "reasons": reasons, "spent_micro_usd": ledger.spent, "model_calls": ledger.calls,
            "spent_by_step": ledger.steps, "search_queries": ledger.search_queries, "free": result.get("free"),
            "batches": [{k: b.get(k) for k in ("step", "requests", "state", "waited_seconds", "estimated")}
                        for b in result.get("batches") or []],
            "known_programmes": sum(1 for e in result["events"] if e.get("known_programme")),
            "chronology_from_dossier": sum(1 for f in facts if f["kind"] == "T" and f.get("origin") == "dossier"),
            "chronology_new_ok": sum(1 for f in facts if f["kind"] == "T" and f.get("origin") == "new" and f["status"] == "ok"),
            "dossier_rows_added": sum((e.get("dossier") or {}).get("added", 0) for e in result["events"]),
            "dossier_errors": sum(1 for e in result["events"] if (e.get("dossier") or {}).get("error")),
            "documents_markdown": sum(1 for e in result["events"] for d in e.get("documents") or [] if d["storage"] == "markdown"),
            "documents_total": sum(len(e.get("documents") or []) for e in result["events"]),
            "held_for_owner": len(result.get("held_for_owner") or []),
            "publish_errors": sum(1 for e in result["events"] if e.get("publish_error")),
            "stop": result.get("stop")}


def main(argv=None):
    from ..synthesis.live import connect
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day-from", required=True)
    parser.add_argument("--day-to", required=True)
    parser.add_argument("--limit", type=int, default=0, help="first N items only (0 = all)")
    parser.add_argument("--source", choices=["events", "items"], default="events",
                        help="events = items that reached the old writer; items = every item with a body")
    parser.add_argument("--batch", action="store_true", help="each model phase as one Batch request (-50%%)")
    parser.add_argument("--no-dossier-write", action="store_true")
    parser.add_argument("--publish-hold", action="store_true",
                        help="write CANDIDATEs to the feed tables as HOLD owner_review (not public)")
    parser.add_argument("--run-ref", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--schema-mode", choices=["schema", "text"], default="text")  # kept for the workflow input
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    doctrine_records = json.loads(DOCTRINE.read_text(encoding="utf-8"))["records"]
    ledger = Ledger()
    model = Gemini(os.environ.get("GEMINI_API_KEY"), ledger, batch=args.batch)
    cheap = FreeModel(model)  # free model when CLOUDFLARE_* secrets exist, Gemini otherwise
    with connect() as conn:
        with conn.cursor() as cur:
            items = (load_events if args.source == "events" else load_items)(cur, args.day_from, args.day_to)
            if args.limit:
                items = items[:args.limit]
            by_event = {it["event_id"]: confirmed_companies(cur, [it["event_id"]]) for it in items if it["event_id"]}
            conn.commit()
            store = DbStore(cur)

            def save(ev, rec, facts, cards):
                if args.no_dossier_write or not ev["group"].get("programme"):
                    return {"added": 0, "skipped": True}
                try:
                    out = store.save(ev["group"]["programme"], [ev["group"]["programme"]], ev["topic"],
                                     (ev["research"].get("parent") or {}).get("summary"), facts, cards,
                                     args.run_ref, PILOT_VERSION)
                    conn.commit()
                    return out
                except Exception:
                    conn.rollback()
                    raise

            try:
                result = run(cur, model, items, by_event, doctrine_records, out_dir, store=store,
                             run_ref=args.run_ref, save=save, cheap=cheap,
                             template=os.environ.get("KB_TEMPLATE", "1") != "0")
            except BudgetStop as stop:
                conn.rollback()
                result = {"version": PILOT_VERSION, "items": len(items), "groups": 0, "events": [], "stop": str(stop)}
            except Exception as exc:  # the spend and batch names are still written and printed
                conn.rollback()
                result = {"version": PILOT_VERSION, "items": len(items), "groups": 0, "events": [],
                          "stop": "CRASH " + type(exc).__name__ + ":" + str(exc)[:160], "batches": model.batches}
            if args.publish_hold:
                from .publish import publish_hold
                for rec in result["events"]:
                    if rec.get("outcome") != "CANDIDATE":
                        continue
                    try:
                        rec["synthesis_id"] = publish_hold(cur, rec, args.run_ref)
                        conn.commit()
                    except Exception as exc:  # one event's write fails alone
                        conn.rollback()
                        rec["publish_error"] = type(exc).__name__ + ":" + str(exc)[:160]
                result["held_for_owner"] = [r["synthesis_id"] for r in result["events"] if r.get("synthesis_id")]
    result["spent_micro_usd"], result["search_queries"] = ledger.spent, ledger.search_queries
    result["spent_by_step"] = ledger.steps
    (out_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("KB_PILOT_RESULT " + canonical(summary(result, ledger)))
    return 1 if result.get("stop") else 0
