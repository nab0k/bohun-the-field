"""Pilot syntheses into the feed tables (D-20261030-STAGE-A-CLOSE).

A CANDIDATE of the pilot becomes knowledge.event + synthesis + synthesis_version + synthesis_admission with
decision HOLD / owner_review and public_release false: nothing is visible outside. The owner picks what goes to the
feed; only then the admission becomes PUBLISH (public.export_synthesis_feed shows PUBLISH + public_release only).
"""
import json

from ..synthesis.contracts import canonical, sha
from .prompts import SECTIONS

MAX_HEADLINE = 90  # knowledge.synthesis_version CHECK
MAX_SOURCES = 5


def headline_of(text):
    text = " ".join((text or "").split())
    if len(text) <= MAX_HEADLINE:
        return text
    cut = text[:MAX_HEADLINE - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + "…"


def section_text(items):
    """Section as plain lines (chronology and supply chain keep one line per step)."""
    lines = [" ".join((x.get("text") or "").split()) for x in items or [] if (x.get("text") or "").strip()]
    return "\n".join(lines) or None


def version_of(rec):
    out = rec["synthesis"]
    version = {s: section_text(out.get(s)) for s in SECTIONS}
    version["why_it_matters"] = version["why_it_matters"] or ""
    version["headline"] = headline_of(out.get("headline") or rec["titles"][0])
    return version


def sources_of(rec):
    """Primary source first, then the news items; 1..5 entries."""
    out, seen = [], set()
    primary = rec.get("primary_source") or {}
    if isinstance(primary, dict) and (primary.get("url") or "").startswith(("http://", "https://")):
        out.append({"url": primary["url"], "outlet": primary.get("title") or primary.get("issuer") or "",
                    "date": primary.get("date") or "", "role": "primary", "class": "P1"})
        seen.add(primary["url"])
    for n in rec["news"]:
        if n.get("url") and n["url"] not in seen:
            out.append({"url": n["url"], "outlet": n.get("outlet") or "", "date": n.get("date") or "",
                        "role": "report", "class": "P2"})
            seen.add(n["url"])
    return out[:MAX_SOURCES]


def publish_hold(cur, rec, run_ref):
    """Write one CANDIDATE as a held synthesis; returns the synthesis id."""
    version, sources = version_of(rec), sources_of(rec)
    if not version["what_happened"] or not sources:
        raise ValueError("NOT_PUBLISHABLE")
    link = "single_document" if len(rec["item_ids"]) == 1 else "hard_features"
    cur.execute("""INSERT INTO knowledge.event (event_type, title, link_method, first_published_at)
                   VALUES (%s, %s, %s, %s::date) RETURNING id""",
                (rec.get("type") or "event", version["headline"][:500], link, rec["date"]))
    event_id = cur.fetchone()[0]
    if rec.get("topic"):
        cur.execute("""INSERT INTO knowledge.event_topic (event_id, topic_id, is_main)
                       SELECT %s, id, true FROM knowledge.topic WHERE slug = %s""", (event_id, rec["topic"]))
    for item_id in rec["item_ids"]:
        cur.execute("""INSERT INTO knowledge.event_document (event_id, item_id, relation, is_primary_source, source_class)
                       VALUES (%s, %s, 'report', false, 'P2')""", (event_id, item_id))
    cur.execute("""INSERT INTO knowledge.synthesis (event_id, active_version, active_origin)
                   VALUES (%s, 1, 'machine') RETURNING id""", (event_id,))
    synthesis_id = cur.fetchone()[0]
    cur.execute("""INSERT INTO knowledge.synthesis_version (synthesis_id, version, headline, what_happened, context,
                     policy_direction, bigger_picture, supply_chain, why_it_matters, sources, content_hash, origin,
                     generator) VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'machine',%s)""",
                (synthesis_id, version["headline"], version["what_happened"], version["context"],
                 version["policy_direction"], version["bigger_picture"], version["supply_chain"],
                 version["why_it_matters"], json.dumps(sources, ensure_ascii=False), sha(canonical(version)),
                 "kb-pilot|" + (run_ref or "local")))
    cur.execute("""INSERT INTO knowledge.synthesis_admission (synthesis_id, version, decision, reason_codes,
                     rules_version, checker, public_release) VALUES (%s, 1, 'HOLD', %s, 'kb-pilot-v1',
                     'auto-admission', false)""", (synthesis_id, ["owner_review"]))
    return synthesis_id
